import os

# o SDK A2A lê esta variável só ao ser importado: ela tem de vir antes de qualquer import dele
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")
