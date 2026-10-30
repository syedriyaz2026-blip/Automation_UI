"""Selenium page objects for independently maintainable EPFO portal sections."""

from .authentication_page import AuthenticationPage
from .identity_page import IdentityPage
from .employment_page import EmploymentPage
from .result_page import ResultPage

__all__ = ["AuthenticationPage", "IdentityPage", "EmploymentPage", "ResultPage"]
