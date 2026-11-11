import rayo


def test_version_is_defined() -> None:
    assert isinstance(rayo.__version__, str)
