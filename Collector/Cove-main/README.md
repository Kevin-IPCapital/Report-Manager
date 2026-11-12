# Cove Data Protection collector

Reads the Cove API and appends one snapshot per customer to the `cove` PostgreSQL schema. It never changes Cove.

    pip install -r requirements.txt
    psql "$COVE_PG_DSN" -f sql/schema.sql        # creates the schema, tables and the customers list
    # add customers:  INSERT INTO cove.customers (customer_name) VALUES ('Acme');
    python cove_db.py customers                  # list the customers that will be loaded
    python cove_db.py load-active                # one snapshot per active customer (what the dashboard worker runs)

Configuration is in `.env` (see `.env.example`). In the dashboard it is the `cove_backup` job in
`backend/collector_jobs.json` (your copy of `collector_jobs.example.json`); the example has it disabled. It has no dry-run mode, and the dashboard refuses to pass one.
