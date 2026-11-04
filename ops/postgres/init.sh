#!/bin/sh
# Roda uma vez na criação do cluster. Senhas vêm dos arquivos do volume secrets-db.
set -eu
S=/run/secrets/db
psql -v ON_ERROR_STOP=1 -U postgres -d "$POSTGRES_DB" <<SQL
\set etl \`cat $S/etl_owner_password\`
\set app \`cat $S/agent_app_password\`
\set ops \`cat $S/ops_audit_password\`
CREATE ROLE etl_owner LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'etl';
CREATE ROLE agent_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'app';
CREATE ROLE ops_audit LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'ops';
REVOKE ALL ON DATABASE $POSTGRES_DB FROM PUBLIC;
GRANT CONNECT ON DATABASE $POSTGRES_DB TO etl_owner, agent_app, ops_audit;
GRANT CREATE ON DATABASE $POSTGRES_DB TO etl_owner;
SQL
