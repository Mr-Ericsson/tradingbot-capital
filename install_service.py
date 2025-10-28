#!/usr/bin/env python3
"""
Install auto_close_positions as Windows Service
"""

import sys
import os
import subprocess


def install_service():
    """Install auto-close as Windows service using nssm"""

    # Path to your Python executable and script
    python_exe = sys.executable
    script_path = os.path.join(os.getcwd(), "auto_close_positions.py")

    print("För att installera som Windows Service:")
    print("\n1. Ladda ner NSSM (Non-Sucking Service Manager):")
    print("   https://nssm.cc/download")
    print("\n2. Kör följande kommandon som Administrator:")
    print(f'   nssm install CapitalAutoClose "{python_exe}"')
    print(
        f'   nssm set CapitalAutoClose Arguments "{script_path} --account-mode demo --close-offset 30"'
    )
    print(f'   nssm set CapitalAutoClose AppDirectory "{os.getcwd()}"')
    print(
        f'   nssm set CapitalAutoClose DisplayName "Capital.com Auto Close Positions"'
    )
    print(
        f'   nssm set CapitalAutoClose Description "Automatically close all positions 30 minutes before market close"'
    )
    print(f"   nssm start CapitalAutoClose")

    print("\n3. För att ta bort servicen:")
    print("   nssm stop CapitalAutoClose")
    print("   nssm remove CapitalAutoClose confirm")


if __name__ == "__main__":
    install_service()
