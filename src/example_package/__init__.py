"""Example package. Rename it to your project's import name."""

__version__ = "0.1.0"

from example_package.core import greet
from example_package.greetings import Greeting, GreetingNotFoundError, GreetingStore

__all__ = ["Greeting", "GreetingNotFoundError", "GreetingStore", "__version__", "greet"]
