"""
Extract Structured Data Skill
Extract tables, lists, and structured content from HTML.
"""

from typing import Any, Dict, Optional

from bs4 import BeautifulSoup

from app.agents.skills.context import SkillContext


async def extract_structured_data(
    ctx: SkillContext,
    html: str,
    selector: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract structured data from HTML.

    Args:
        ctx: Skill execution context
        html: HTML content to parse
        selector: Optional CSS selector to focus on specific elements

    Returns:
        Extracted tables, lists, and key data
    """
    soup = BeautifulSoup(html, "html.parser")

    if selector:
        elements = soup.select(selector)
        soup = BeautifulSoup("".join(str(e) for e in elements), "html.parser")

    # Extract tables
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for row in table.find_all("tr"):
            cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)

    # Extract lists
    lists = []
    for ul in soup.find_all(["ul", "ol"]):
        items = [li.get_text(strip=True) for li in ul.find_all("li")]
        if items:
            lists.append(items)

    return {
        "tables": tables[:5],  # Limit to 5 tables
        "lists": lists[:10],  # Limit to 10 lists
        "tables_count": len(tables),
        "lists_count": len(lists),
    }
