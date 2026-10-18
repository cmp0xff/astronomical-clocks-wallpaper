#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CLI wrapper for aggregating battery protocol phase records into a report."""

import sys

from host.battery.cli import main_report

if __name__ == "__main__":
    sys.exit(main_report())
