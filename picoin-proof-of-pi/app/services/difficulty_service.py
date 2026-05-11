import math
from typing import List, Dict, Any, Tuple

class DifficultyService:
    """
    Servicio de Consenso PICOIN (vOptimized):
    Implementa ajuste de dificultad basado en SMA de 10 bloques y Position-Weighted Difficulty.
    """
    
    TARGET_BLOCK_MS = 60000  # 60 segundos objetivo
    SMA_WINDOW = 10          # Ventana de suavizado
    MIN_SAMPLE_COUNT = 16    # Suelo de seguridad criptográfica
    MAX_ADJUSTMENT = 1.25    # Cap de ajuste (+25% / -20%)
    PI_PIVOT_POS = 10000     # Posición de referencia para normalización

    @staticmethod
    def calculate_next_difficulty(history: List[Dict[str, Any]], current_params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Calcula los nuevos parámetros del protocolo basándose en el historial reciente.
        
        Args:
            history: Lista de los últimos N bloques con 'total_task_ms' y 'range_start'.
            current_params: Diccionario con 'segment_size', 'sample_count' y 'max_pi_position'.
        """
        if len(history) < DifficultyService.SMA_WINDOW:
            return current_params, {"action": "wait", "reason": "insufficient history", "adjustment_factor": 1.0}

        # 1. SUAVIZADO (SMA)
        # Extraemos la ventana de los últimos 10 bloques para evitar oscilaciones por anomalías de red
        recent_window = history[-DifficultyService.SMA_WINDOW:]
        avg_observed_ms = sum(b['total_task_ms'] for b in recent_window) / DifficultyService.SMA_WINDOW

        # 2. COMPENSACIÓN DE POSICIÓN (Position-Weighted)
        # El algoritmo BBP es O(n log n) o superior dependiendo de la implementación. 
        # Normalizamos el tiempo observado basándonos en la profundidad media de la ventana.
        avg_pos = sum(b['range_start'] for b in recent_window) / DifficultyService.SMA_WINDOW
        
        # Factor de profundidad: log10(pos) comparado con el pivot de diseño
        # Usamos max(avg_pos, 100) para evitar logs negativos o infinitos en el génesis
        depth_factor = math.log10(max(avg_pos, 100)) / math.log10(DifficultyService.PI_PIVOT_POS)
        
        # Tiempo normalizado: ¿Cuánto tardaría este mismo trabajo en la posición pivot?
        # Si el tiempo observado es alto pero la posición es muy profunda, el normalized_ms será menor.
        normalized_ms = avg_observed_ms / depth_factor

        # 3. RATIO DE AJUSTE
        # Si normalized_ms > TARGET, necesitamos bajar la dificultad (ratio < 1)
        adjustment_ratio = DifficultyService.TARGET_BLOCK_MS / normalized_ms
        
        # Aplicamos límites de seguridad para evitar ataques de manipulación de timestamp
        adjustment_ratio = max(0.8, min(DifficultyService.MAX_ADJUSTMENT, adjustment_ratio))

        # 4. ACTUALIZACIÓN DE PARÁMETROS (Prioridad: segment_size)
        new_params = current_params.copy()
        
        # Calculamos la "potencia de trabajo" deseada
        target_segment_size = current_params['segment_size'] * adjustment_ratio
        
        # Si el ajuste es moderado, solo tocamos el segment_size
        if 32 <= target_segment_size <= 512:
            new_params['segment_size'] = int(target_segment_size)
        else:
            # Si el segment_size se sale de rangos óptimos, ajustamos sample_count
            # manteniendo siempre el suelo de seguridad de 16
            if target_segment_size < 32:
                new_params['segment_size'] = 32
                # Intentamos compensar reduciendo muestras si el ratio es muy bajo
                potential_samples = int(current_params['sample_count'] * adjustment_ratio)
                new_params['sample_count'] = max(DifficultyService.MIN_SAMPLE_COUNT, potential_samples)
            else:
                # Si la red es muy potente, subimos segment_size primero y luego muestras
                new_params['segment_size'] = 512
                new_params['sample_count'] = int(current_params['sample_count'] * (target_segment_size / 512))

        # 5. RECALCULO DE MÉTRICA DE DIFICULTAD (Para UI/Dashboard)
        # Mantenemos la estructura de la fórmula v0.16 pero con los nuevos valores
        new_params['difficulty'] = (
            (new_params['segment_size'] / 64) * 
            (new_params['sample_count'] / 8) * 
            (math.log10(current_params['max_pi_position']) / math.log10(10000))
        )

        # Metadatos para el test y el log del nodo
        action = "keep"
        if adjustment_ratio > 1.05: action = "increase"
        elif adjustment_ratio < 0.95: action = "decrease"

        meta = {
            "action": action,
            "reason": f"SMA-10 Optimized (Obs: {int(avg_observed_ms)}ms, Norm: {int(normalized_ms)}ms)",
            "adjustment_factor": round(adjustment_ratio, 4)
        }

        return new_params, meta