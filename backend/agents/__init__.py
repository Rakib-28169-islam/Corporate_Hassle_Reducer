"""
Agents Module - Dedicated AI Workers
Each agent is a specialist connected to its own tools.
All agents inherit from BaseAgent for shared operations.
"""

from .base_agent import BaseAgent
from .gmail_agent import GmailAgent
from .slack_agent import SlackAgent
from .outlook_agent import OutlookAgent
