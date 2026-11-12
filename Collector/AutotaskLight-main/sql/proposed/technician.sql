-- PROPOSED, NOT APPLIED. Needs your approval, because it changes the live autotask schema.
--
-- The Autotask collector does not currently capture who a ticket is assigned to: it requests a fixed list of ticket fields
-- (TICKET_REPORT_FIELDS) that has no assignee, and autotask.ticket has no assignee column. The dashboard is already prepared:
-- it checks on every request whether the two things below exist and shows technician data as soon as they do.
--
-- 1. Run this file (it is safe to run twice).
-- 2. Change the collector (not done yet; needs a working Autotask API to verify the field names):
--      - add "assignedResourceID" to TICKET_REPORT_FIELDS in autotask_client.py
--      - copy it into the row built in company_tickets_report
--      - add "assigned_resource_id" to TICKET_COLUMNS and to the ticket row in autotask_dataset.py
--      - load the Resources entity (id, firstName, lastName, isActive) into the resource table below
-- Until step 2 is done the new column stays empty and the dashboard shows tickets as "Unassigned".

ALTER TABLE autotask.ticket ADD COLUMN IF NOT EXISTS assigned_resource_id BIGINT;   -- Autotask Tickets.assignedResourceID

CREATE TABLE IF NOT EXISTS autotask.resource (
    resource_id BIGINT      PRIMARY KEY,        -- Autotask Resources.id
    first_name  TEXT,
    last_name   TEXT,
    is_active   BOOLEAN,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ticket_assigned_resource_idx ON autotask.ticket (assigned_resource_id);
