import rayo_package


def test_version_is_defined() -> None:
    assert isinstance(rayo_package.__version__, str)
