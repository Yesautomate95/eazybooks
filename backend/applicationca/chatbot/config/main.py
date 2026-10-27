"""
    This file contains all project configs read from env file.
"""

import os
from applicationca.chatbot.constants.eazybooks_secrets import open_ai_key

class Base(object):
    """
    Base configuration class. Contains all the default configurations.
    """

    DEBUG: bool = True

class Config(Base):
    """
    Main configuration class. Contains all the configurations for the project.
    """

    DEBUG: bool = True
    OPENAI_API_KEY: str = open_ai_key
    ASSISTANT_ID: str = os.getenv("ASSISTANT_ID")
    OPENAI_MODEL: str = "gpt-4.1-mini"


config = Config()