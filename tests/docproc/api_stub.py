"""Stand-ins for the standalone extraction app's HTTP test helpers.

Most of the ported tests exercise the engine directly. A few drove that app's own FastAPI layer (workspaces, /extractions), which is not part
of this backend (its role is played by app/api/user_documents.py, tested in tests/test_user_docs.py and tests/test_api_settings_docs.py), so
those tests skip instead of failing."""
import pytest

H = {}
SKIP = "drives the standalone extraction app's HTTP layer, which is not part of this backend"


@pytest.fixture()
def client():
    pytest.skip(SKIP)


@pytest.fixture()
def pdf(tmp_path):
    from tests.docproc.make_packet import build
    p = tmp_path / "p.pdf"
    build(str(p))
    return p


def _skip(*a, **k):
    pytest.skip(SKIP)


mk = up = wait = reload_app = _skip
