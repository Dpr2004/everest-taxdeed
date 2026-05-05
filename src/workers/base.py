"""Classe base para workers. Padroniza logging, DB, tratamento de erro."""
from datetime import datetime
from src.db.connection import cursor
from src.utils.logger import get_logger


class BaseWorker:
    name = "base"

    def __init__(self):
        self.logger = get_logger(self.name)
        self.run_id = None
        self.items_processed = 0
        self.errors_count = 0
        # Workers podem setar candidates_count via execute() pra revelar
        # silent failures (queries que retornaram N lotes mas processaram 0).
        # Quando candidates_count > 0 e items_processed == 0 -> degraded.
        self.candidates_count = 0

    def start_run(self):
        with cursor() as cur:
            cur.execute(
                "INSERT INTO run_logs (worker, status) VALUES (?, ?)",
                (self.name, "running"),
            )
            self.run_id = cur.lastrowid
        self.logger.info(f"Run {self.run_id} iniciado ({self.name})")

    def finish_run(self, status="success", log_text=""):
        with cursor() as cur:
            cur.execute(
                "UPDATE run_logs SET finished_at = CURRENT_TIMESTAMP, status = ?, "
                "items_processed = ?, errors_count = ?, log_text = ? WHERE id = ?",
                (status, self.items_processed, self.errors_count, log_text, self.run_id),
            )
        self.logger.info(
            f"Run {self.run_id} finalizado: {status} | {self.items_processed} itens | "
            f"{self.errors_count} erros"
        )

    def execute(self):
        """Implementado pelos workers concretos. Deve popular items_processed e errors_count."""
        raise NotImplementedError

    def run(self):
        self.start_run()
        log_lines = []
        try:
            self.execute()
            # FAIL LOUD: candidates>0 mas items=0 indica falha silenciosa
            # (token expirado, parser quebrado, todos sites bloqueando, etc).
            # Status precisa refletir isso pra alarmar Daniel em vez de
            # esconder atras de "success" enganoso.
            if self.candidates_count > 0 and self.items_processed == 0:
                msg = (f"DEGRADED: {self.candidates_count} candidatos retornados "
                       f"pela query mas 0 processados ({self.errors_count} erros). "
                       f"Provavel: secret faltando, parser quebrado ou todos sites "
                       f"bloqueando. Investigar ANTES do proximo run.")
                log_lines.append(msg)
                self.logger.error(msg)
                self.finish_run("degraded", "\n".join(log_lines))
            elif self.candidates_count > 0 and self.items_processed < self.candidates_count * 0.1:
                msg = (f"DEGRADED: so {self.items_processed}/{self.candidates_count} "
                       f"({100*self.items_processed/self.candidates_count:.0f}%) processados. "
                       f"Esperado >10%. Provavel parser quebrado pra maioria dos condados.")
                log_lines.append(msg)
                self.logger.warning(msg)
                self.finish_run("degraded", "\n".join(log_lines))
            else:
                self.finish_run("success", "\n".join(log_lines))
        except Exception as e:
            self.errors_count += 1
            log_lines.append(f"FATAL: {e}")
            self.logger.exception("Erro fatal no worker")
            self.finish_run("failed", "\n".join(log_lines))
            raise
