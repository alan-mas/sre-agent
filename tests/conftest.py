"""Tests never trace to LangSmith: it would burn the free-tier trace quota and pollute the project."""

import os

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
