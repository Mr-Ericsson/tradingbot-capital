# bot_loop.py
# Kör din pipeline i en evig loop:
# 1) capital_scan_tradeable.py
# 2) rank_top10_momentum.py
# 3) analyze_edge_score.py
# 4) place_pending_orders.py  (respekterar MAX_TRADES inuti filen)
#
# - Lämnar alla delskript fristående (kör via subprocess)
# - Enkel loggning till bot_loop.log
# - Lätt att stoppa med Ctrl+C
# - Liten paus mellan API-stegen för att undvika rate limits

import subprocess
import sys
import time
import os
from datetime import datetime

# ==== KONFIGURERA HÄR ====
LOOP_INTERVAL_SECONDS = 600  # 10 minuter mellan körningar
PYTHON_BIN = sys.executable  # använder samma Python som kör detta
LOG_FILE = "bot_loop.log"
SLEEP_BETWEEN_STEPS = 1.2  # liten paus mellan skript (sekunder)
# ==========================

SCRIPTS = [
    ["capital_scan_tradeable.py"],
    ["rank_top10_momentum.py"],  # skapar top10_momentum_current.csv
    ["analyze_edge_score.py"],  # skapar orders.csv
    ["place_pending_orders.py"],  # lägger ordrar (respekterar MAX_TRADES inuti)
]


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_script(script_argv: list[str]) -> int:
    """
    Kör ett av delskripten med samma Python-interpreter.
    Returnerar exit code (0 = OK).
    """
    cmd = [PYTHON_BIN] + script_argv
    log(f"→ Kör: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )

        # Skriv vidare skriptens stdout/stderr till vår log
        if result.stdout:
            for line in result.stdout.splitlines():
                log(f"STDOUT: {line}")
        if result.stderr:
            for line in result.stderr.splitlines():
                log(f"STDERR: {line}")
        if result.returncode == 0:
            log(f"✓ Klart: {' '.join(script_argv)} (exit 0)")
        else:
            log(f"✗ Fel: {' '.join(script_argv)} (exit {result.returncode})")
        return result.returncode
    except Exception as e:
        log(f"✗ Undantag vid körning av {script_argv}: {e}")
        return 1


def main_loop():
    log("=== START bot_loop ===")
    try:
        while True:
            t0 = time.time()
            log("=== Ny körning start ===")

            for args in SCRIPTS:
                rc = run_script(args)
                time.sleep(SLEEP_BETWEEN_STEPS)
                # Om ett steg failar – hoppa över resten denna runda
                if rc != 0:
                    log("Avbryter resterande steg pga fel. Väntar till nästa loop…")
                    break

            dt = time.time() - t0
            log(f"=== Körning klar på {dt:.1f}s ===")
            # Vänta ut kvarvarande tid av intervallet
            sleep_left = max(0, LOOP_INTERVAL_SECONDS - dt)
            if sleep_left > 0:
                log(f"Väntar {sleep_left:.1f}s till nästa körning…")
                time.sleep(sleep_left)
    except KeyboardInterrupt:
        log("Avslutas (Ctrl+C) – hejdå! 👋")
    except Exception as e:
        log(f"Kritiskt fel i loop: {e}")
    finally:
        log("=== STOP bot_loop ===")


if __name__ == "__main__":
    main_loop()
