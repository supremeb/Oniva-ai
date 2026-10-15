"""Static lint of deployment manifests (U10 / R11): required env keys, probes, image pins."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "k8s"

REQUIRED_API_ENV = {
    "SECRET_KEY", "ENCRYPTION_SALT", "DATABASE_URL", "SYSTEM_DATABASE_URL",
    "MIGRATION_DATABASE_URL", "APP_DATABASE_PASSWORD", "SYSTEM_DATABASE_PASSWORD",
    "REDIS_URL", "REDIS_PASSWORD",
}


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _all_k8s() -> list[dict]:
    docs = []
    for f in sorted(K8S.glob("*.yaml")):
        docs.extend(_docs(f))
    return docs


def _find(kind: str, name: str) -> dict:
    for d in _all_k8s():
        if d.get("kind") == kind and d["metadata"].get("name") == name:
            return d
    raise AssertionError(f"{kind}/{name} not found in k8s/")


def _container(deploy: dict, name: str) -> dict:
    return next(c for c in deploy["spec"]["template"]["spec"]["containers"] if c["name"] == name)


def test_all_manifests_parse():
    assert _all_k8s()
    yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())


def test_api_env_keys_and_expansion_order():
    api = _container(_find("Deployment", "oniva-api"), "api")
    names = [e["name"] for e in api["env"]]
    secrets = set(names)
    configmap = _find("ConfigMap", "oniva-config")["data"]
    for key in REQUIRED_API_ENV:
        assert key in secrets, f"{key} missing from api env"
    assert "CORS_ORIGINS" in configmap and configmap["CORS_ORIGINS"] != "*"
    assert configmap["AUTO_MIGRATE"] == "false"
    # $(VAR) only expands variables defined EARLIER in `env` (never envFrom).
    for i, e in enumerate(api["env"]):
        for ref in re.findall(r"\$\(([A-Z_]+)\)", e.get("value", "")):
            assert ref in names[:i], f"{e['name']} references $({ref}) before it is defined"


def test_secret_template_has_every_referenced_key():
    keys = set(_find("Secret", "oniva-secrets")["stringData"])
    for d in _all_k8s():
        if d.get("kind") not in ("Deployment", "Job"):
            continue
        spec = d["spec"]["template"]["spec"]
        for c in spec.get("containers", []) + spec.get("initContainers", []):
            for e in c.get("env", []):
                ref = e.get("valueFrom", {}).get("secretKeyRef")
                if ref and not ref.get("optional"):
                    assert ref["key"] in keys, f"{c['name']}: secret key {ref['key']} not in secrets.yaml"


def test_api_probes_use_live_and_ready():
    api = _container(_find("Deployment", "oniva-api"), "api")
    assert api["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert api["readinessProbe"]["httpGet"]["path"] == "/health/ready"


def test_migration_job_runs_alembic_as_owner():
    job = _find("Job", "oniva-migrate")
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert c["command"][:3] == ["alembic", "upgrade", "head"]
    urls = {e["name"]: e.get("value", "") for e in c["env"]}
    assert "//oniva:" in urls["MIGRATION_DATABASE_URL"]  # owner, not oniva_app/oniva_system


def test_redis_requires_password():
    redis = _container(_find("Deployment", "redis"), "redis")
    assert "--requirepass" in redis["command"]
    assert any(e["name"] == "REDISCLI_AUTH" for e in redis["env"])


def test_no_latest_image_tags_and_kustomize_pins_images():
    for d in _all_k8s():
        if d.get("kind") not in ("Deployment", "Job"):
            continue
        spec = d["spec"]["template"]["spec"]
        for c in spec.get("containers", []) + spec.get("initContainers", []):
            assert not c["image"].endswith(":latest"), c["image"]
    kust = yaml.safe_load((K8S / "kustomization.yaml").read_text())
    assert {i["name"] for i in kust["images"]} >= {"oniva", "oniva-frontend"}
    assert all(not i["newTag"] == "latest" for i in kust["images"])
    for res in kust["resources"]:
        assert (K8S / res).exists(), res


def test_postgres_init_configmap_matches_script():
    cm = _find("ConfigMap", "postgres-init")["data"]["01-roles.sh"]
    assert cm == (ROOT / "postgres/init/01-roles.sh").read_text()


def test_prod_compose_wiring():
    c = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    app = c["services"]["app"]
    assert "ports" not in app, "fixed host port conflicts with replicas"
    env = c["x-app-env"]
    for key in ("DATABASE_URL", "SYSTEM_DATABASE_URL", "MIGRATION_DATABASE_URL", "ENCRYPTION_SALT",
                "CORS_ORIGINS", "SESSION_BASE_PATH"):
        assert key in env
    assert app["environment"]["AUTO_MIGRATE"] == "false"
    assert app["read_only"] is True and any(v.endswith(":/data") for v in app["volumes"])
    assert "/health/live" in " ".join(app["healthcheck"]["test"])
    assert "migrate" in c["services"] and "migrate" in app["depends_on"]
