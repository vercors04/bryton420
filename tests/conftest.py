from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"
EX_BON = DATA / "ex_bon"
REFERENCE_FIT = EX_BON / "PlanTrip" / "De Uzer à Soyons.fit"
REFERENCE_TRACKS = EX_BON / "Tracks" / "De Uzer à Soyons"


@pytest.fixture(scope="session")
def reference_fit():
    return REFERENCE_FIT.read_bytes()


def data(name):
    return (DATA / name).read_bytes()
