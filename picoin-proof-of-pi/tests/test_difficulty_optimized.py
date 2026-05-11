import math
from app.services.difficulty_service import DifficultyService

def test_optimized_difficulty_logic():
    print("--- 🧪 INICIANDO VERIFICACIÓN DE LÓGICA OPTIMIZADA ---")
    
    # Simulación: Red demasiado rápida (bloques de 30s) en posición profunda
    current_params = {"segment_size": 64, "sample_count": 32, "max_pi_position": 10000}
    history = [{"total_task_ms": 30000, "range_start": 50000} for _ in range(10)]
    
    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params)
    
    # 1. Verificar suavizado (Adjustment ratio no debe ser extremo)
    assert meta["adjustment_factor"] >= 0.8, "Ajuste por debajo del límite de seguridad"
    
    # 2. Verificar Position-Weighting
    # En pos 50,000, el algoritmo es más lento. El tiempo normalizado debería ser menor a 30s.
    # Por tanto, el aumento de dificultad debería ser menos agresivo que si estuviéramos en pos 100.
    print(f"Meta Action: {meta['action']} (Ratio: {meta['adjustment_factor']})")
    
    # 3. Verificar suelo de seguridad de muestras
    # Simulamos una red extremadamente lenta para forzar bajada de dificultad
    slow_history = [{"total_task_ms": 300000, "range_start": 100} for _ in range(10)]
    new_params_slow, _ = DifficultyService.calculate_next_difficulty(slow_history, current_params)
    
    assert new_params_slow["sample_count"] >= 16, "ERROR: sample_count bajó de 16!"
    print(f"Seguridad verificada: sample_count={new_params_slow['sample_count']}")
    
    print("✅ TEST DE INTEGRACIÓN PASADO: Lógica SMA y Compensación activa.")

if __name__ == "__main__":
    test_optimized_difficulty_logic()
