-- Aplicado como etl_owner DEPOIS do COPY e das contagens (FORCE antes do COPY bloqueia a carga de
-- um dono que não é superusuário; depois dele o dono lê 0 linhas de canon). Idempotente.
-- Policies só com funções de `util`; nenhuma de agent_rw referencia `canon`. Catálogo global: sem RLS.
REVOKE ALL ON SCHEMA canon, agent_rw, util FROM PUBLIC;
GRANT USAGE ON SCHEMA canon, agent_rw, util TO agent_app;
GRANT USAGE ON SCHEMA agent_rw, util TO ops_audit;
GRANT USAGE ON SEQUENCE agent_rw.protocol_request_seq TO agent_app;

ALTER TABLE canon.brand ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.brand FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.brand FOR SELECT TO agent_app USING (family = ANY(util.current_brands()));
ALTER TABLE canon.unit ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.unit FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.unit FOR SELECT TO agent_app USING (brand_family = ANY(util.current_brands()));
ALTER TABLE canon.course_offer ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.course_offer FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.course_offer FOR SELECT TO agent_app USING (brand_family = ANY(util.current_brands()));
ALTER TABLE canon.enrollment ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.enrollment FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.enrollment FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.enrollment_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.enrollment_event FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.enrollment_event FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.term_enrollment ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.term_enrollment FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.term_enrollment FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.class_result ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.class_result FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.class_result FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.person ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.person FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.person FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.lms_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.lms_account FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.lms_account FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.lms_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.lms_event FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.lms_event FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.lms_quiz ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.lms_quiz FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.lms_quiz FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.lms_quiz_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.lms_quiz_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.lms_quiz_attempt FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.protocol ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.protocol FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.protocol FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.protocol_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.protocol_event FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.protocol_event FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.issued_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.issued_document FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.issued_document FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.protocol_message ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.protocol_message FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.protocol_message FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.contract ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.contract FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.contract FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.invoice ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.invoice FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.invoice FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.payment ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.payment FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.payment FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.late_charge ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.late_charge FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.late_charge FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.discount ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.discount FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.discount FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.agreement ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.agreement FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.agreement FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.agreement_invoice ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.agreement_invoice FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.agreement_invoice FOR SELECT TO agent_app USING (person_key = util.current_person());
ALTER TABLE canon.price ENABLE ROW LEVEL SECURITY;
ALTER TABLE canon.price FORCE ROW LEVEL SECURITY;
CREATE POLICY scope ON canon.price FOR SELECT TO agent_app USING (brand_family = ANY(util.current_brands()));

GRANT SELECT ON ALL TABLES IN SCHEMA canon TO agent_app;

-- agent_rw: persiste entre cargas, então as policies são recriadas.
ALTER TABLE agent_rw.protocol_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_rw.protocol_request FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sel ON agent_rw.protocol_request;
DROP POLICY IF EXISTS ins ON agent_rw.protocol_request;
CREATE POLICY sel ON agent_rw.protocol_request FOR SELECT TO agent_app USING (person_key = util.current_person());
CREATE POLICY ins ON agent_rw.protocol_request FOR INSERT TO agent_app WITH CHECK (person_key = util.current_person());

ALTER TABLE agent_rw.feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_rw.feedback FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sel ON agent_rw.feedback;
DROP POLICY IF EXISTS ins ON agent_rw.feedback;
CREATE POLICY sel ON agent_rw.feedback FOR SELECT TO agent_app USING (person_key = util.current_person());
CREATE POLICY ins ON agent_rw.feedback FOR INSERT TO agent_app WITH CHECK (person_key = util.current_person());

-- turn_output: agent_app só insere; ops_audit lê o não expirado; a purga (etl_owner) enxerga e apaga só
-- o expirado. Quem lê/purga precisa de `SET app.now` (sem missing_ok: esquecê-lo falha alto).
-- Limite conhecido: o TTL é consultivo contra uma sessão ops_audit maliciosa (ela pode fazer
-- `SET app.now` e ler expirados); a regra do relógio proíbe ler a hora no SQL. As linhas expiradas são
-- removidas fisicamente pela purga do etl_owner.
ALTER TABLE agent_rw.turn_output ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_rw.turn_output FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ins ON agent_rw.turn_output;
DROP POLICY IF EXISTS audit ON agent_rw.turn_output;
DROP POLICY IF EXISTS purge_sel ON agent_rw.turn_output;
DROP POLICY IF EXISTS purge_del ON agent_rw.turn_output;
CREATE POLICY ins ON agent_rw.turn_output FOR INSERT TO agent_app WITH CHECK (person_key = util.current_person());
CREATE POLICY audit ON agent_rw.turn_output FOR SELECT TO ops_audit USING (expires_at > current_setting('app.now')::timestamptz);
CREATE POLICY purge_sel ON agent_rw.turn_output FOR SELECT TO etl_owner USING (expires_at <= current_setting('app.now')::timestamptz);
CREATE POLICY purge_del ON agent_rw.turn_output FOR DELETE TO etl_owner USING (expires_at <= current_setting('app.now')::timestamptz);

-- Grants de etl/grants.yaml.
REVOKE ALL ON ALL TABLES IN SCHEMA agent_rw FROM agent_app, ops_audit;
GRANT SELECT, INSERT ON agent_rw.protocol_request, agent_rw.feedback TO agent_app;
GRANT INSERT ON agent_rw.turn_output TO agent_app;
GRANT SELECT ON agent_rw.turn_output TO ops_audit;
