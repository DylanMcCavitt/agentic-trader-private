#!/usr/bin/env python3
"""Print the effective config: config.json deep-merged with config.local.json.

The untracked config.local.json holds the real account_number; tracked
config.json carries the REPLACE_ME placeholder.
"""
import json

from order_gate import load_config

if __name__ == "__main__":
    print(json.dumps(load_config(), indent=2))
