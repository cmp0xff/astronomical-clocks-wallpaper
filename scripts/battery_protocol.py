#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CLI wrapper for the battery and resource acceptance protocol."""

import sys

from host.battery.cli import main

if __name__ == "__main__":
    sys.exit(main())
