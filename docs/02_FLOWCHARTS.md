# 2. Interaction diagrams and flowcharts

Diagrams reflect the **assembled code**; differences in a deployed environment need validation. They are standard [Mermaid](https://mermaid.js.org/) and are also stored as `.mmd` files in `diagrams/`.

## A. Logical architecture

```mermaid
flowchart TB
  subgraph External[Externally managed systems]
    AT[Autotask PSA API]
    NC[N-central API]
    S1[SentinelOne API]
    CV[Cove API]
  end
  subgraph Ingest[Collectors - separate processes]
    A[Autotask collector]
    N[N-central inventory / patches / monitoring]
    S[SentinelOne monthly collector]
    C[Cove collector]
  end
  subgraph DB[PostgreSQL]
    ATS[(autotask)]
    NCS[(ncentral)]
    S1S[(sentinelone)]
    CVS[(cove)]
    RM[("report_manager: users, clients, links, weights, snapshots")]
  end
  subgraph Ops[Running collection]
    SCH[run_scheduler or Windows Task Scheduler]
    W[collection_worker]
    AL[collection_alerts]
    Q[("SQLite queue + heartbeat")]
  end
  subgraph App[Application]
    API[Django API]
    UI[Preact dashboard]
    PDF[Browser PDF builder]
  end
  AT --> A --> ATS
  NC --> N --> NCS
  S1 --> S --> S1S
  CV --> C --> CVS
  ATS --> API
  NCS --> API
  S1S --> API
  CVS -. later .-> API
  RM <--> API
  API <--> UI --> PDF
  UI -->|queue a run| API --> Q
  SCH --> W --> Q
  W --> A
  W --> N
  W --> S
  W --> C
  W -->|after patch run| RM
  SCH --> AL --> Q
```

## B. Manual rerun and worker lifecycle

```mermaid
sequenceDiagram
  participant U as Staff user
  participant R as Data Collection page
  participant D as Django collector API
  participant Q as SQLite queue
  participant S as Scheduler (task or run_scheduler)
  participant W as collection_worker
  participant C as run_collection / collector script
  participant P as PostgreSQL source schema
  U->>R: Rerun or Update All
  R->>D: POST /api/collectors/run
  D->>D: Authenticate, staff check, allowlist
  D->>Q: enqueue batch if none active
  D-->>R: 202 queued, batch_id
  loop polling
    R->>D: GET /api/collectors/execution
    D->>Q: batch, schedules, heartbeat
    D-->>R: state
  end
  S->>W: tick (every minute)
  W->>Q: heartbeat, claim batch (exclusive lock)
  loop each job
    W->>C: run_collection job (child process)
    C->>P: write source records
    loop while it runs
      W->>Q: heartbeat every 30 seconds
    end
    C-->>W: exit code + log
    W->>Q: SUCCESS / PARTIAL / FAILED + duration
  end
  W->>Q: finish batch, heartbeat IDLE
```

## C. Worker tick decisions

```mermaid
flowchart TD
  A[Scheduler tick] --> B{Worker lock free?}
  B -- No --> Z[Record BUSY heartbeat, exit normally]
  B -- Yes --> H[Record TICK heartbeat]
  H --> I[Mark interrupted batches failed]
  I --> C{Batch queued?}
  C -- Yes --> F[Claim it]
  C -- No --> D[Find due scheduled jobs]
  D --> E{Any due?}
  E -- No --> X[Record IDLE, exit]
  E -- Yes --> F
  F --> G[Run next job as child process, heartbeat every 30 s]
  G --> O{Outcome}
  O -- complete --> S1[SUCCESS]
  O -- devices N-central cannot find --> S2[PARTIAL]
  O -- fatal or timeout --> S3[FAILED]
  S1 --> P{N-central patches?}
  S2 --> P
  S3 --> L
  P -- Yes --> SN[Save patch score snapshots]
  P -- No --> L
  SN --> L{More jobs?}
  L -- Yes --> G
  L -- No --> M[Finish batch, record IDLE]
```

## D. Alerts

```mermaid
flowchart TD
  T[collection_alerts every 5 min] --> H[Read job logs, stored data age, schedules, heartbeat]
  H --> R[Apply rules: worker silent, failed, stuck, overdue, no data]
  R --> P[Compare with alert state]
  P --> N{Anything new, due for reminder, or cleared?}
  N -- No --> X[Exit quietly]
  N -- Yes --> E{Recipients configured?}
  E -- No --> W[Warn, record nothing]
  E -- Yes --> M[Send one email]
  M --> OK{Sent?}
  OK -- Yes --> S[Record what was announced]
  OK -- No --> F[Record nothing, retry next run]
```

## E. Dashboard request

```mermaid
sequenceDiagram
  participant B as Browser
  participant D as Django API
  participant PG as PostgreSQL
  B->>D: GET /api/sections/patches?client=3
  D->>D: Validate token and parameters
  D->>PG: org units linked to client 3
  D->>PG: newest and previous usable inventories for those devices
  D->>PG: devices grouped by customer (client filter choices)
  PG-->>D: counts per type and category
  D->>D: Score with this user's weights over the shared weights
  D-->>B: types, scores, overall, client choices
  B->>D: GET /api/sections/tickets?from=...&to=...
  D->>PG: newest version of each ticket, created in range
  D-->>B: ticket rows
  B->>B: filter, group, average, sort, paginate in the browser
```

## F. Authentication

```mermaid
flowchart TD
  L[POST /api/auth/login] --> H{X-Requested-With header?}
  H -- No --> R[403]
  H -- Yes --> T{Too many recent failures?}
  T -- Yes --> R2[429]
  T -- No --> B{Dev bypass enabled and DEBUG on?}
  B -- Yes --> D[Create or reuse user, password not checked]
  B -- No --> A[Django authenticate]
  A --> P{Valid?}
  P -- No --> U[401, count the failure]
  P -- Yes --> K[Clear failures]
  D --> K
  K --> TK[Access token in the body, rotating refresh cookie]
```

## G. Client resolution

```mermaid
flowchart TD
  S[Read customer lists: Autotask, N-central, SentinelOne, Cove] --> U[Drop customers already linked by ID]
  U --> G[Group by identical words in identical order]
  G --> M{Matches an existing client name or alias?}
  M -- Yes --> X[Offer to add to that client, flag as inference]
  M -- No --> Y{More than one customer?}
  Y -- Yes --> I[Offer a new client, flag as inference]
  Y -- No --> O[Offer a new client, nothing inferred]
  X --> C[Person confirms]
  I --> C
  O --> C
  C --> L[Create or extend client, link each customer by its own ID, other spellings kept as aliases]
  L --> D[Discrepancies: client missing from a source, or link to a customer that no longer exists]
```

## H. Patch scores and trend

```mermaid
flowchart LR
  R[N-central patch collection succeeds or is partial] --> W[Worker saves snapshots]
  W --> A[All devices, as of that run]
  W --> B[Each client linked to N-central]
  A --> S[("api_patchsnapshot: counts, shared weights, scores")]
  B --> S
  S --> T[GET patch-trend: one point per day]
  U[GET patches] --> V[Scores with the caller's weights]
  S -. never changed by user weights .-> V
```

## I. Report configuration and PDF export

```mermaid
flowchart LR
  U[Open a saved report] --> S[Choose the dates]
  J[Imported JSON template] --> S
  S --> A[Fetch ticket, patch and health endpoints]
  A --> F[Apply filters, compute series]
  F --> V{Data available?}
  V -- Yes --> P[KPI cards and charts]
  V -- No --> N[Unavailable state, nothing invented]
  P --> PDF[Generate A4 PDF in the browser]
  S --> E[Export template as JSON]
  N --> PDF
```

## J. Main relationships

This is a summary. The complete entity-relationship diagrams, generated from the real definitions, are in [`09_ER_DIAGRAMS.md`](09_ER_DIAGRAMS.md).

```mermaid
erDiagram
  AUTOTASK_COMPANY ||--o{ AUTOTASK_TICKET : scoped_to
  AUTOTASK_TICKET ||--o{ AUTOTASK_TICKET_NOTE : has
  NCENTRAL_PATCH_RUN ||--o{ NCENTRAL_DEVICE_PATCH : includes
  NCENTRAL_PATCH_RUN ||--o{ NCENTRAL_PATCH_ENTRY : includes
  NCENTRAL_DEVICE }o--|| NCENTRAL_CUSTOMERS : belongs_to
  SENTINELONE_SNAPSHOT ||--o{ SENTINELONE_AGENT : captures
  COVE_SNAPSHOT ||--o{ COVE_DEVICE : captures
  API_CLIENT ||--o{ API_CLIENTSOURCEMAP : linked_by
  API_CLIENT ||--o{ API_CLIENTALIAS : known_as
  API_CLIENT ||--o{ API_PATCHSNAPSHOT : scored_in
  AUTH_USER ||--o{ API_SCOREWEIGHT : owns
  AUTH_USER ||--o{ API_REFRESHTOKEN : holds
```

`API_CLIENTSOURCEMAP` links by ID across schemas (`source` + `external_id`) and is not a database foreign key to the source tables. Consult [`04_DATA_MODEL.md`](04_DATA_MODEL.md) before changing constraints.
