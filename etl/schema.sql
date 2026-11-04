-- Schemas do banco. Aplicado como etl_owner a cada carga: `canon` é recriado; `agent_rw` persiste
-- (CREATE IF NOT EXISTS), sem FK nem policy que referencie `canon`. Colunas de `canon` = campos de
-- ontology/entities.yaml + colunas implícitas (person_key, enrollment_key, brand_family); o teste
-- test_schema_matches_ontology confere. Tipos: id/code/enum/text=text, money=bigint (centavos),
-- number=numeric, instant=timestamptz. RLS, policies e grants: rls.sql (depois do COPY).
DROP SCHEMA IF EXISTS canon CASCADE;
CREATE SCHEMA canon;

CREATE TABLE canon.brand (
  brand text,
  modality text,
  hq_uf text,
  active boolean,
  family text
);
CREATE TABLE canon.unit (
  brand_family text not null,
  unit_id text,
  brand text,
  label text,
  unit_type text,
  uf text
);
CREATE TABLE canon.course (
  course_id text,
  acronym text,
  name_pt text,
  degree text,
  duration_semesters numeric
);
CREATE TABLE canon.curriculum (
  curriculum_id text,
  course_id text,
  valid_from_term text,
  valid_to_term text,
  total_hours numeric,
  status text
);
CREATE TABLE canon.subject (
  subject_code text,
  name_pt text,
  hours numeric,
  credits numeric,
  subject_type text
);
CREATE TABLE canon.curriculum_subject (
  curriculum_id text,
  subject_code text,
  semester numeric,
  mandatory boolean,
  prerequisite_code text
);
CREATE TABLE canon.course_offer (
  brand_family text not null,
  offer_id text,
  unit_id text,
  course_id text,
  curriculum_id text,
  shift text,
  modality text
);
CREATE TABLE canon.term (
  term text,
  start_date date,
  end_date date,
  av1_start date,
  av2_start date,
  is_current boolean
);
CREATE TABLE canon.enrollment (
  person_key uuid not null,
  enrollment_key uuid,
  brand text,
  ra text,
  course_id text,
  curriculum_id text,
  unit_label text,
  modality text,
  shift text,
  entry_type text,
  enrolled_on date,
  entry_term text,
  semester numeric,
  completion_pct numeric,
  gpa numeric,
  status text,
  status_date date,
  origin_brand text
);
CREATE INDEX ON canon.enrollment (person_key);
CREATE TABLE canon.enrollment_event (
  person_key uuid not null,
  enrollment_key uuid,
  term text,
  event_date date,
  old_status text,
  new_status text,
  reason text,
  protocol_ref text
);
CREATE INDEX ON canon.enrollment_event (person_key);
CREATE TABLE canon.term_enrollment (
  person_key uuid not null,
  enrollment_key uuid,
  term text,
  semester_level numeric,
  registration_date date,
  term_status text,
  credits_enrolled numeric
);
CREATE INDEX ON canon.term_enrollment (person_key);
CREATE TABLE canon.class_result (
  person_key uuid not null,
  enrollment_key uuid,
  class_id text,
  curriculum_id text,
  is_retake boolean,
  grade_av1 numeric,
  grade_av2 numeric,
  grade_av3 numeric,
  final_grade numeric,
  attendance_pct numeric,
  result text,
  professor text,
  subject_code text,
  subject_name_pt text,
  term text,
  section text,
  divergencia boolean
);
CREATE INDEX ON canon.class_result (person_key);
CREATE TABLE canon.person (
  person_key uuid not null,
  first_name text
);
CREATE INDEX ON canon.person (person_key);
CREATE TABLE canon.lms_account (
  person_key uuid not null,
  enrollment_key uuid,
  lms_user_id text,
  suspended boolean,
  first_access timestamptz,
  last_access timestamptz,
  created_at timestamptz
);
CREATE INDEX ON canon.lms_account (person_key);
CREATE TABLE canon.lms_event (
  person_key uuid not null,
  enrollment_key uuid,
  event_id text,
  event_name text,
  occurred_at timestamptz,
  subject_name_pt text
);
CREATE INDEX ON canon.lms_event (person_key);
CREATE TABLE canon.lms_quiz (
  person_key uuid not null,
  enrollment_key uuid,
  quiz_id text,
  quiz_type text,
  opens_at timestamptz,
  closes_at timestamptz,
  time_limit_s numeric,
  attempts_allowed numeric,
  subject_name_pt text
);
CREATE INDEX ON canon.lms_quiz (person_key);
CREATE TABLE canon.lms_quiz_attempt (
  person_key uuid not null,
  enrollment_key uuid,
  attempt_id text,
  quiz_id text,
  attempt_no numeric,
  state text,
  started_at timestamptz,
  finished_at timestamptz,
  sumgrades numeric
);
CREATE INDEX ON canon.lms_quiz_attempt (person_key);
CREATE TABLE canon.protocol (
  person_key uuid not null,
  enrollment_key uuid,
  protocol text,
  service_code text,
  status text,
  channel text,
  opened_at timestamptz,
  first_response_at timestamptz,
  sla_due_at timestamptz,
  closed_at timestamptz,
  resolution text,
  solicitante_papel text
);
CREATE INDEX ON canon.protocol (person_key);
CREATE TABLE canon.protocol_event (
  person_key uuid not null,
  enrollment_key uuid,
  protocol text,
  seq numeric,
  from_status text,
  to_status text,
  changed_at timestamptz
);
CREATE INDEX ON canon.protocol_event (person_key);
CREATE TABLE canon.service (
  code text,
  name_pt text,
  area text,
  sla_hours numeric,
  fee_cents bigint,
  active boolean,
  ui_label text,
  mode text
);
CREATE TABLE canon.issued_document (
  person_key uuid not null,
  enrollment_key uuid,
  document_id text,
  protocol text,
  document_type text,
  issued_at timestamptz,
  codigo_validacao text,
  delivery text
);
CREATE INDEX ON canon.issued_document (person_key);
CREATE TABLE canon.protocol_message (
  person_key uuid not null,
  enrollment_key uuid,
  message_id text,
  protocol text,
  autor_papel text,
  modelo text,
  created_at timestamptz,
  texto_livre text
);
CREATE INDEX ON canon.protocol_message (person_key);
CREATE TABLE canon.contract (
  person_key uuid not null,
  enrollment_key uuid,
  contract_id text,
  product_code text,
  signed_on date,
  start_competence text,
  end_competence text,
  due_day numeric,
  status text,
  payer_type text
);
CREATE INDEX ON canon.contract (person_key);
CREATE TABLE canon.invoice (
  person_key uuid not null,
  enrollment_key uuid,
  invoice_id text,
  contract_id text,
  competence text,
  invoice_type text,
  issue_date date,
  due_date date,
  gross_cents bigint,
  discount_cents bigint,
  net_cents bigint,
  status text,
  protocol_ref text,
  agreement_id text
);
CREATE INDEX ON canon.invoice (person_key);
CREATE TABLE canon.payment (
  person_key uuid not null,
  enrollment_key uuid,
  payment_id text,
  invoice_id text,
  paid_at timestamptz,
  credit_date date,
  amount_cents bigint,
  principal_cents bigint,
  fine_cents bigint,
  interest_cents bigint,
  method text,
  status text
);
CREATE INDEX ON canon.payment (person_key);
CREATE TABLE canon.late_charge (
  person_key uuid not null,
  enrollment_key uuid,
  late_charge_id text,
  invoice_id text,
  reference_date date,
  days_late numeric,
  fine_rate numeric,
  monthly_interest_rate numeric,
  fine_cents bigint,
  interest_cents bigint,
  waived boolean
);
CREATE INDEX ON canon.late_charge (person_key);
CREATE TABLE canon.discount (
  person_key uuid not null,
  enrollment_key uuid,
  discount_id text,
  contract_id text,
  discount_type text,
  rate numeric,
  valid_from date,
  valid_to date
);
CREATE INDEX ON canon.discount (person_key);
CREATE TABLE canon.agreement (
  person_key uuid not null,
  enrollment_key uuid,
  agreement_id text,
  contract_id text,
  created_at timestamptz,
  original_debt_cents bigint,
  waived_cents bigint,
  agreed_amount_cents bigint,
  installments numeric,
  status text,
  protocol_ref text
);
CREATE INDEX ON canon.agreement (person_key);
CREATE TABLE canon.agreement_invoice (
  person_key uuid not null,
  enrollment_key uuid,
  agreement_id text,
  invoice_id text
);
CREATE INDEX ON canon.agreement_invoice (person_key);
CREATE TABLE canon.price (
  course_id text,
  brand_family text,
  ano numeric,
  mensalidade bigint,
  modality text,
  parcelas_por_semestre numeric
);
CREATE TABLE canon.coverage (
  source text,
  domain text,
  starts_on date,
  ends_on date
);

CREATE SCHEMA IF NOT EXISTS agent_rw;
CREATE SEQUENCE IF NOT EXISTS agent_rw.protocol_request_seq;
-- A lista do CHECK repete AGENT_SERVICES (services.yaml, mode=agent); test_check_matches_ontology_agent_services confere.
CREATE TABLE IF NOT EXISTS agent_rw.protocol_request (
  id text primary key,
  person_key uuid not null,
  enrollment_index text not null,
  service_code text not null check (service_code in ('DOC-001', 'DOC-002', 'DOC-003', 'DOC-040', 'DOC-041', 'ACA-011', 'ACA-020', 'ACA-021', 'FIN-030', 'FIN-031', 'FIN-032', 'TEC-060', 'GEN-090', 'OUV-099')),
  subject_code text,
  description text,
  draft_id text unique not null,
  opened_on date not null,
  prazo date not null
);
CREATE TABLE IF NOT EXISTS agent_rw.feedback (
  trace_id text,
  person_key uuid,
  useful boolean,
  comment text
);
CREATE TABLE IF NOT EXISTS agent_rw.turn_output (
  trace_id text primary key,
  person_key uuid not null,
  created_at timestamptz not null,
  expires_at timestamptz not null,
  payload jsonb not null
);
