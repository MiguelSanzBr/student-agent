# Prova temporal do congelamento

- `eval-frozen.sha`: SHA da tag `eval-frozen` (`d6e3d8c`), carimbado no OpenTimestamps antes da rodada de teste cega e da janela C. O `.ots` já tem atestação no Bitcoin (`ots upgrade` completo); `ots verify` exige um nó Bitcoin local ou um verificador público.
- `blind-attacks.sha`: SHA `68e5bb8` (eval-frozen mais a correção do carregador do holdout de ataques), carimbado antes da 1ª tentativa dos ataques cegos. A rodada válida terminou em `80c5ace`, que só acrescenta a correção do cabeçalho A2A no cliente de avaliação (ver `quality/rounds.jsonl` e ARCHITECTURE, seção 11). O `.ots` estava pendente no calendário quando foi commitado; `ots upgrade` completa.
