# Compose project ramascene_jobs; the image records the engine commit it was
# built from. Needs DATASETS_DIR (the EXIOBASE matrices, mounted read-only).
export ENGINE_COMMIT := `git -C .. rev-parse HEAD 2>/dev/null || echo unknown`
export ENGINE_BRANCH := `git -C .. branch --show-current 2>/dev/null || echo unknown`

# Build and start the service in the background.
up:
    docker compose -p ramascene_jobs up -d --build --wait


# Stop it and remove the containers.
down:
    docker compose -p ramascene_jobs down

# Follow the service logs.
logs:
    docker compose -p ramascene_jobs logs -f
