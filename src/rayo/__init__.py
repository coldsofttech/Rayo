"""Example package. Rename it to your project's import name."""

__version__ = "0.1.0"

from rayo.core import greet
from rayo.greetings import Greeting, GreetingNotFoundError, GreetingStore

__all__ = ["Greeting", "GreetingNotFoundError", "GreetingStore", "__version__", "greet"]
