"""Códigos JSON-RPC do A2A 1.0 (a2a-sdk 1.3.0) observados no spike da Task 3."""

TASK_NOT_FOUND = -32001  # GetTask/CancelTask/SendMessage(taskId) de tarefa alheia ou inexistente
TASK_NOT_CANCELABLE = -32002  # CancelTask de tarefa concluída
PUSH_UNSUPPORTED = -32003  # os 4 métodos de push config com push_notifications=False
UNSUPPORTED = -32004  # SendStreamingMessage/SubscribeToTask com streaming=False
# GetExtendedAgentCard com extended_agent_card=False: UNSUPPORTED (-32004), não -32007.
EXTENDED_CARD_UNSUPPORTED = -32004
EXTENDED_CARD_NOT_CONFIGURED = (
    -32007
)  # só com extended_agent_card=True sem card estendido (ver spike)
INTERNAL_ERROR = -32603
METHOD_NOT_FOUND = -32601
