#!/usr/bin/env python3
"""Hugging Face compatibility entrypoint.

The native HF implementation lives in :mod:`app_hf`; keeping this small
wrapper means Spaces configured with the historical ``app.py`` filename still
run the same no-guardian deployment.
"""

from app_hf import main


if __name__ == "__main__":
    main()
