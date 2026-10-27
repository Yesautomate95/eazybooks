"""
Mock Tally Server
=================
A comprehensive mock implementation of Tally's XML HTTP API for development on macOS.
Simulates all Tally operations without needing actual Tally ERP software.

Usage:
    python -m mock_tally.server

Or import and run programmatically:
    from mock_tally.server import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
"""

__version__ = "1.0.0"
