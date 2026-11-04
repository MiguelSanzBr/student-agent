-- Funções das policies. Estáveis entre cargas (o `DROP SCHEMA canon CASCADE` não as toca) e sem
-- referência a `canon`. `app.person_key` só é definido pela API, a partir do `sub` do JWT; vazio ou
-- ausente (ao fim de uma transação o valor local volta a '') vira NULL e a policy não casa nada.
CREATE SCHEMA IF NOT EXISTS util;

CREATE OR REPLACE FUNCTION util.current_person() RETURNS uuid
  LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.person_key', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION util.current_brands() RETURNS text[]
  LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.brands', true), '')::text[] $$;
