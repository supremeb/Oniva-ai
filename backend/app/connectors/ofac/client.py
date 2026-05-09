"""
OFAC SDN Client
Fetches and parses OFAC Specially Designated Nationals (SDN) list from Treasury.gov
"""

import httpx
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field
import asyncio
import logging
import re
from pathlib import Path
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# OFAC Public Data URLs (updated to new location as of 2024)
OFAC_SDN_XML_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"
OFAC_SDN_CSV_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.csv"
OFAC_CONSOLIDATED_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/consolidated.xml"

# Legacy URLs (redirect to new location)
OFAC_LEGACY_XML_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"


@dataclass
class SDNEntry:
    """A single SDN (Specially Designated National) entry"""
    uid: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: str = ""
    entry_type: str = ""  # 'Individual', 'Entity', 'Vessel', 'Aircraft'
    programs: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    addresses: List[Dict[str, str]] = field(default_factory=list)
    nationalities: List[str] = field(default_factory=list)
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    citizenship: List[str] = field(default_factory=list)
    id_numbers: List[Dict[str, str]] = field(default_factory=list)
    remarks: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "entry_type": self.entry_type,
            "programs": self.programs,
            "aliases": self.aliases,
            "addresses": self.addresses,
            "nationalities": self.nationalities,
            "date_of_birth": self.date_of_birth,
            "place_of_birth": self.place_of_birth,
            "citizenship": self.citizenship,
            "id_numbers": self.id_numbers,
            "remarks": self.remarks,
        }


@dataclass
class SDNSearchResult:
    """Result from SDN search"""
    entry: SDNEntry
    match_score: float
    match_type: str  # 'exact', 'fuzzy', 'alias'
    matched_name: str


class OFACClient:
    """
    Client for fetching and searching OFAC SDN data.
    Implements caching to avoid repeated downloads.
    """

    # Cache settings
    CACHE_DURATION_HOURS = 24

    def __init__(self, cache_dir: Optional[str] = None):
        self._entries: List[SDNEntry] = []
        self._last_fetch: Optional[datetime] = None
        self._cache_dir = Path(cache_dir) if cache_dir else Path("/tmp/ofac_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._is_loaded = False
        self._lock = asyncio.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded and len(self._entries) > 0

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    async def load_data(self, force_refresh: bool = False) -> bool:
        """
        Load SDN data, using cache if available and not expired.

        Args:
            force_refresh: Force download even if cache is valid

        Returns:
            True if data loaded successfully
        """
        async with self._lock:
            # Check if we already have data and it's recent
            if self._is_loaded and not force_refresh:
                if self._last_fetch and (datetime.utcnow() - self._last_fetch).total_seconds() < self.CACHE_DURATION_HOURS * 3600:
                    logger.info(f"Using cached OFAC data with {len(self._entries)} entries")
                    return True

            # Try to load from file cache
            cache_file = self._cache_dir / "sdn_cache.xml"
            cache_meta = self._cache_dir / "sdn_meta.txt"

            if not force_refresh and cache_file.exists() and cache_meta.exists():
                try:
                    # Check cache age
                    with open(cache_meta, 'r') as f:
                        cache_time = datetime.fromisoformat(f.read().strip())

                    if (datetime.utcnow() - cache_time).total_seconds() < self.CACHE_DURATION_HOURS * 3600:
                        logger.info("Loading OFAC data from file cache")
                        await self._parse_xml_file(str(cache_file))
                        self._last_fetch = cache_time
                        self._is_loaded = True
                        logger.info(f"Loaded {len(self._entries)} SDN entries from cache")
                        return True
                except Exception as e:
                    logger.warning(f"Failed to load cache: {e}")

            # Download fresh data
            logger.info("Downloading fresh OFAC SDN data...")
            try:
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    response = await client.get(OFAC_SDN_XML_URL)
                    response.raise_for_status()

                    xml_content = response.text

                    # Save to cache
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        f.write(xml_content)
                    with open(cache_meta, 'w') as f:
                        f.write(datetime.utcnow().isoformat())

                    # Parse
                    await self._parse_xml_content(xml_content)
                    self._last_fetch = datetime.utcnow()
                    self._is_loaded = True

                    logger.info(f"Downloaded and parsed {len(self._entries)} SDN entries")
                    return True

            except Exception as e:
                logger.error(f"Failed to download OFAC data: {e}")
                # Fall back to any existing cache
                if cache_file.exists():
                    logger.warning("Falling back to stale cache")
                    await self._parse_xml_file(str(cache_file))
                    self._is_loaded = True
                    return True
                return False

    async def _parse_xml_file(self, filepath: str) -> None:
        """Parse SDN XML from file"""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        await self._parse_xml_content(content)

    async def _parse_xml_content(self, xml_content: str) -> None:
        """Parse SDN XML content into entries"""
        self._entries = []

        try:
            root = ET.fromstring(xml_content)

            # Extract namespace from root tag if present
            namespace = None
            if root.tag.startswith('{'):
                namespace = root.tag[1:root.tag.index('}')]
                logger.info(f"Detected XML namespace: {namespace}")

            # Find all sdnEntry elements
            if namespace:
                entries = root.findall(f'.//{{{namespace}}}sdnEntry')
            else:
                entries = root.findall('.//sdnEntry')

            logger.info(f"Found {len(entries)} SDN entries to parse")

            for entry_elem in entries:
                entry = self._parse_entry(entry_elem, namespace)
                if entry:
                    self._entries.append(entry)

            logger.info(f"Successfully parsed {len(self._entries)} entries")

        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")
            raise

    def _parse_entry(self, elem: ET.Element, namespace: Optional[str] = None) -> Optional[SDNEntry]:
        """Parse a single SDN entry from XML element"""
        def get_text(element: Optional[ET.Element]) -> Optional[str]:
            return element.text.strip() if element is not None and element.text else None

        def find(parent: ET.Element, tag: str) -> Optional[ET.Element]:
            # Try with namespace first, then without
            if namespace:
                result = parent.find(f'{{{namespace}}}{tag}')
                if result is not None:
                    return result
            return parent.find(tag)

        def findall(parent: ET.Element, tag: str) -> List[ET.Element]:
            if namespace:
                results = parent.findall(f'{{{namespace}}}{tag}')
                if results:
                    return results
            return parent.findall(tag)

        uid = get_text(find(elem, 'uid'))
        if not uid:
            return None

        entry = SDNEntry(uid=uid)

        # Basic info
        entry.first_name = get_text(find(elem, 'firstName'))
        entry.last_name = get_text(find(elem, 'lastName'))
        entry.entry_type = get_text(find(elem, 'sdnType')) or "Unknown"

        # Build full name
        if entry.first_name and entry.last_name:
            entry.full_name = f"{entry.first_name} {entry.last_name}"
        elif entry.last_name:
            entry.full_name = entry.last_name
        elif entry.first_name:
            entry.full_name = entry.first_name

        # Programs
        program_list = find(elem, 'programList')
        if program_list is not None:
            for prog in findall(program_list, 'program'):
                if prog.text:
                    entry.programs.append(prog.text.strip())

        # Aliases (AKAs)
        aka_list = find(elem, 'akaList')
        if aka_list is not None:
            for aka in findall(aka_list, 'aka'):
                first = get_text(find(aka, 'firstName'))
                last = get_text(find(aka, 'lastName'))
                if first and last:
                    entry.aliases.append(f"{first} {last}")
                elif last:
                    entry.aliases.append(last)
                elif first:
                    entry.aliases.append(first)

        # Addresses
        addr_list = find(elem, 'addressList')
        if addr_list is not None:
            for addr in findall(addr_list, 'address'):
                address = {}
                for field in ['address1', 'address2', 'address3', 'city', 'stateOrProvince', 'postalCode', 'country']:
                    value = get_text(find(addr, field))
                    if value:
                        address[field] = value
                if address:
                    entry.addresses.append(address)

        # Nationalities
        nationality_list = find(elem, 'nationalityList')
        if nationality_list is not None:
            for nat in findall(nationality_list, 'nationality'):
                country = find(nat, 'country')
                if country is not None and country.text:
                    entry.nationalities.append(country.text.strip())

        # Date of Birth
        dob_list = find(elem, 'dateOfBirthList')
        if dob_list is not None:
            for dob in findall(dob_list, 'dateOfBirthItem'):
                dob_text = get_text(find(dob, 'dateOfBirth'))
                if dob_text:
                    entry.date_of_birth = dob_text
                    break

        # Place of Birth
        pob_list = find(elem, 'placeOfBirthList')
        if pob_list is not None:
            for pob in findall(pob_list, 'placeOfBirthItem'):
                pob_text = get_text(find(pob, 'placeOfBirth'))
                if pob_text:
                    entry.place_of_birth = pob_text
                    break

        # ID Numbers
        id_list = find(elem, 'idList')
        if id_list is not None:
            for id_elem in findall(id_list, 'id'):
                id_info = {}
                id_type = get_text(find(id_elem, 'idType'))
                id_number = get_text(find(id_elem, 'idNumber'))
                id_country = get_text(find(id_elem, 'idCountry'))
                if id_type:
                    id_info['type'] = id_type
                if id_number:
                    id_info['number'] = id_number
                if id_country:
                    id_info['country'] = id_country
                if id_info:
                    entry.id_numbers.append(id_info)

        # Remarks
        entry.remarks = get_text(find(elem, 'remarks'))

        return entry

    def search(
        self,
        name: str,
        date_of_birth: Optional[str] = None,
        country: Optional[str] = None,
        threshold: float = 0.85,
        max_results: int = 10
    ) -> List[SDNSearchResult]:
        """
        Search SDN list for matches.

        Args:
            name: Name to search for
            date_of_birth: Optional DOB to narrow results
            country: Optional country to filter
            threshold: Minimum match score (0-1)
            max_results: Maximum number of results

        Returns:
            List of matching entries with scores
        """
        if not self._is_loaded:
            logger.warning("SDN data not loaded. Call load_data() first.")
            return []

        results: List[SDNSearchResult] = []
        name_clean = self._normalize_name(name)

        for entry in self._entries:
            best_score = 0.0
            best_match_type = "fuzzy"
            matched_name = ""

            # Check main name
            entry_name_clean = self._normalize_name(entry.full_name)
            score = self._fuzzy_match(name_clean, entry_name_clean)
            if score > best_score:
                best_score = score
                matched_name = entry.full_name
                best_match_type = "exact" if score >= 0.99 else "fuzzy"

            # Check aliases
            for alias in entry.aliases:
                alias_clean = self._normalize_name(alias)
                score = self._fuzzy_match(name_clean, alias_clean)
                if score > best_score:
                    best_score = score
                    matched_name = alias
                    best_match_type = "alias"

            # Apply threshold
            if best_score < threshold:
                continue

            # Apply DOB filter if provided
            if date_of_birth and entry.date_of_birth:
                if not self._match_dob(date_of_birth, entry.date_of_birth):
                    continue

            # Apply country filter if provided
            if country:
                country_upper = country.upper()
                country_match = False
                for nat in entry.nationalities:
                    if country_upper in nat.upper():
                        country_match = True
                        break
                for cit in entry.citizenship:
                    if country_upper in cit.upper():
                        country_match = True
                        break
                for addr in entry.addresses:
                    if country_upper in addr.get('country', '').upper():
                        country_match = True
                        break
                if not country_match:
                    continue

            results.append(SDNSearchResult(
                entry=entry,
                match_score=best_score,
                match_type=best_match_type,
                matched_name=matched_name
            ))

        # Sort by score descending
        results.sort(key=lambda x: x.match_score, reverse=True)

        return results[:max_results]

    def _normalize_name(self, name: str) -> str:
        """Normalize a name for comparison"""
        if not name:
            return ""
        # Remove punctuation, extra spaces, convert to lowercase
        normalized = re.sub(r'[^\w\s]', '', name.lower())
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def _fuzzy_match(self, s1: str, s2: str) -> float:
        """Calculate fuzzy match score between two strings"""
        if not s1 or not s2:
            return 0.0
        return SequenceMatcher(None, s1, s2).ratio()

    def _match_dob(self, search_dob: str, entry_dob: str) -> bool:
        """Check if DOBs match (handles partial dates)"""
        # Normalize to just numbers
        search_nums = re.sub(r'[^\d]', '', search_dob)
        entry_nums = re.sub(r'[^\d]', '', entry_dob)

        # If either is partial, check if the available parts match
        if len(search_nums) >= 4 and len(entry_nums) >= 4:
            # At least compare years
            return search_nums[:4] == entry_nums[:4]

        return search_nums == entry_nums

    def get_entry_by_uid(self, uid: str) -> Optional[SDNEntry]:
        """Get a specific entry by UID"""
        for entry in self._entries:
            if entry.uid == uid:
                return entry
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded data"""
        if not self._is_loaded:
            return {
                "loaded": False,
                "total_entries": 0,
            }

        entry_types = {}
        programs = set()

        for entry in self._entries:
            entry_types[entry.entry_type] = entry_types.get(entry.entry_type, 0) + 1
            programs.update(entry.programs)

        return {
            "loaded": True,
            "total_entries": len(self._entries),
            "entry_types": entry_types,
            "unique_programs": len(programs),
            "sample_programs": list(programs)[:10],
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
        }


# Singleton instance
_ofac_client: Optional[OFACClient] = None


def get_ofac_client() -> OFACClient:
    """Get singleton OFAC client instance"""
    global _ofac_client
    if _ofac_client is None:
        _ofac_client = OFACClient()
    return _ofac_client
