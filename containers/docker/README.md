# Docker client/server containers

```
containers/docker/
├── client/Dockerfile       # interactive gRPC client shell
├── server/Dockerfile       # long-running simulation server
└── docker-compose.yml      # starts both on one private Docker network
```

The containers are deliberately given narrow host filesystem access:

- `server` writes simulation results to the host directory mounted at `/data`
  from `DR_EVT_RESULTS_DIR`.
- Both containers read trace inputs from the host directory mounted read-only
  at `/input` from `DR_EVT_DATA_DIR`. The client streams the rows; the server
  reads only the CSV header during session initialization.

From the repository root, create the two host directories and start the
server:

```bash
mkdir -p results data
DR_EVT_RESULTS_DIR="$PWD/results" DR_EVT_DATA_DIR="$PWD/data" \
  docker compose -f containers/docker/docker-compose.yml up --build -d server
```

Open the interactive client shell (the server is reachable as `server:50051`):

```bash
DR_EVT_RESULTS_DIR="$PWD/results" DR_EVT_DATA_DIR="$PWD/data" \
  docker compose -f containers/docker/docker-compose.yml run --build --rm client
```

Inside that shell, files from the host's `data/` directory are available at
`/input`. For example:

```bash
/opt/dr-evt/bin/dr_evt_client server:50051 /input/my-trace.csv
```

Stop the server when finished:

```bash
docker compose -f containers/docker/docker-compose.yml down
```

Use absolute paths for `DR_EVT_RESULTS_DIR` and `DR_EVT_DATA_DIR` when running
the Compose command outside the repository root.
