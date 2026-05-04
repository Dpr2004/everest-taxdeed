"""Scoring Engine - calcula bid maximo, ROI projetado e decisao para cada lote.

Premissas (default - editaveis via env):
- ROI_MIN: 0.30 (30%)
- MARGEM_SEG: 0.20 (20%)
- REFORMA_SQFT: $35
- HOLDING_MES: $450
- TEMPO_VENDA_MESES: 6
- COMISSAO_VENDA: 0.06
- CLOSING_VENDA: 0.02
- BUFFER: 0.10
- META_LUCRO: $35.000

Decisao:
- LANCE: ROI >= ROI_MIN E lucro >= 70% META_LUCRO
- REVISAR: ROI >= 70% ROI_MIN
- PASSA: caso contrario
"""
import os
from src.db.connection import cursor
from src.workers.base import BaseWorker

ROI_MIN = float(os.environ.get("ROI_MIN", "0.30"))
MARGEM_SEG = float(os.environ.get("MARGEM_SEG", "0.20"))
REFORMA_SQFT = float(os.environ.get("REFORMA_SQFT", "35"))
HOLDING_MES = float(os.environ.get("HOLDING_MES", "450"))
TEMPO_VENDA = float(os.environ.get("TEMPO_VENDA_MESES", "6"))
COMISSAO_VENDA = float(os.environ.get("COMISSAO_VENDA", "0.06"))
CLOSING_VENDA = float(os.environ.get("CLOSING_VENDA", "0.02"))
BUFFER = float(os.environ.get("BUFFER", "0.10"))
META_LUCRO = float(os.environ.get("META_LUCRO", "35000"))


class ScoringEngine(BaseWorker):
    name = "scoring_engine"

    def execute(self):
        with cursor() as cur:
            cur.execute("""
                SELECT l.id, l.parcel_id, l.address, l.min_bid, l.assessed_value,
                       l.just_value, l.building_sqft, l.lot_sqft, l.year_built,
                       l.property_type
                FROM lots l
                JOIN sales s ON s.id = l.sale_id
                WHERE s.sale_date >= DATE('now')
            """)
            lots = cur.fetchall()

        for lot in lots:
            try:
                score_data = self._calcular_score(lot)
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(f"Score falhou lot {lot['id']}: {e}")
                continue
            if score_data is None:
                continue
            with cursor() as cur2:
                cur2.execute("""
                    INSERT INTO scores (lot_id, max_bid_recommended, projected_profit,
                                        projected_roi, final_score, decision, calculated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(lot_id) DO UPDATE SET
                        max_bid_recommended = excluded.max_bid_recommended,
                        projected_profit = excluded.projected_profit,
                        projected_roi = excluded.projected_roi,
                        final_score = excluded.final_score,
                        decision = excluded.decision,
                        calculated_at = CURRENT_TIMESTAMP
                """, (
                    lot["id"], score_data["max_bid"], score_data["profit"],
                    score_data["roi"], score_data["score"], score_data["decision"]
                ))
            self.items_processed += 1
            if score_data["decision"] == "LANCE":
                self.logger.info(
                    f"LANCE lot {lot['id']} ({lot['parcel_id']}): "
                    f"ROI={score_data['roi']*100:.1f}%, lucro=${score_data['profit']:,.0f}, "
                    f"max_bid=${score_data['max_bid']:,.0f}"
                )

    def _calcular_score(self, lot):
        min_bid = lot["min_bid"] or 0
        if min_bid <= 0:
            return None

        # ARV estimado: usar just_value * 0.85 (descontando margem de mercado)
        just = lot["just_value"] or 0
        assessed = lot["assessed_value"] or 0
        if just > 0:
            arv = float(just) * 0.85
        elif assessed > 0:
            arv = float(assessed) * 0.85
        else:
            arv = float(min_bid) * 4 * 0.85

        # Custo reforma estimado: SQFT * REFORMA_SQFT
        sqft = lot["building_sqft"] or 0
        if sqft > 0:
            reforma = sqft * REFORMA_SQFT
        else:
            # Sem dado de SQFT: assumir reforma media baseada em tipo
            ptype = (lot["property_type"] or "").upper()
            if ptype == "LOT":
                reforma = 0  # lotes vagos
            elif ptype == "MOBILE":
                reforma = 8000
            else:
                reforma = 25000

        # Holding cost
        holding = HOLDING_MES * TEMPO_VENDA

        # Custo de venda (comissao + closing)
        custo_venda = (COMISSAO_VENDA + CLOSING_VENDA) * arv

        # Buffer
        buffer = BUFFER * (reforma + holding)

        # Custo total se pagar min_bid
        custo_total_min = min_bid + reforma + holding + custo_venda + buffer

        # Lucro liquido projetado se pagar min_bid
        profit = arv - custo_total_min
        roi = profit / custo_total_min if custo_total_min > 0 else 0

        # Bid maximo recomendado: ARV - todos os custos - meta lucro - margem seg
        max_bid = arv - reforma - holding - custo_venda - buffer - META_LUCRO - (MARGEM_SEG * arv)
        max_bid = max(0, max_bid)

        # Score final 0-100
        roi_component = min(60, (roi / ROI_MIN) * 60) if ROI_MIN > 0 else 0
        profit_component = min(25, (profit / META_LUCRO) * 25) if META_LUCRO > 0 else 0
        confidence_component = 15 if (lot["building_sqft"] and lot["just_value"]) else 5
        score = max(0, roi_component + profit_component + confidence_component)

        # Decisao
        # SAFEGUARD: ratio JV/bid alto (>=4.0) com dados insuficientes (sem
        # property_type/sqft) NUNCA vai pra PASSA silencioso — marcar REVISAR
        # pra LOTES decidir. Caso real 2026-05-04: parcel 162231807902030
        # (1910 Park Manor Dr Orlando, opening $4.6k, assessed $26.6k = ratio
        # 5.78x) ia pra PASSA porque scoring assumia reforma default $25k sem
        # property_type vindo do PA SPA Orange. Daniel viu manualmente que era
        # oportunidade real e flagrou bug.
        bid_v = float(min_bid or 0)
        ratio_jvbid = (float(just) / bid_v) if (just > 0 and bid_v > 0) else (
            (float(assessed) / bid_v) if (assessed > 0 and bid_v > 0) else 0
        )
        dados_insuficientes = not (lot["building_sqft"] and lot["property_type"])

        if roi >= ROI_MIN and profit >= META_LUCRO * 0.7:
            decision = "LANCE"
        elif roi >= ROI_MIN * 0.7:
            decision = "REVISAR"
        elif ratio_jvbid >= 4.0 and dados_insuficientes:
            # Oportunidade potencial mascarada por dados ausentes — manda pra LOTES
            decision = "REVISAR"
        else:
            decision = "PASSA"

        return {
            "max_bid": round(max_bid, 2),
            "profit": round(profit, 2),
            "roi": round(roi, 4),
            "score": round(score, 1),
            "decision": decision,
        }


if __name__ == "__main__":
    ScoringEngine().run()
