#!/bin/bash

# Exit on any error
set -e

# Change to the project root directory
cd "$(dirname "$0")/.."

# Function to display usage
usage() {
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Alembic Commands:"
    echo "  generate <msg>       Auto-generate a new migration with the given message"
    echo "  revision <msg>       Create an empty migration file (without autogenerate)"
    echo "  migrate [rev]        Upgrade database to the newest revision (head), or a specific revision"
    echo "  rollback [rev]       Downgrade the database by 1 revision, or to a specific revision"
    echo "  merge                Merge multiple heads (if branch conflicts exist)"
    echo "  stamp <rev>          Stamp the revision table with the given revision; don't run any migrations"
    echo "  history              Show migration history"
    echo "  current              Show current revision"
    echo ""
    echo "Database Utilities:"
    echo "  shell / psql         Open an interactive PostgreSQL shell in the docker container"
    echo "  clean                Drop all tables in the database (DANGER)"
    echo "  reset                Drop all tables and re-run all migrations (DANGER)"
    echo ""
    exit 1
}

# Check if command is provided
if [ $# -eq 0 ]; then
    usage
fi

COMMAND=$1
shift

case "$COMMAND" in
    generate|makemigrations)
        if [ $# -eq 0 ]; then
            echo "Error: generate command requires a message. Example: $0 generate \"initial migration\""
            exit 1
        fi
        poetry run alembic revision --autogenerate -m "$1"
        ;;
    revision)
        if [ $# -eq 0 ]; then
            echo "Error: revision command requires a message. Example: $0 revision \"Add users table\""
            exit 1
        fi
        poetry run alembic revision -m "$1"
        ;;
    migrate|upgrade)
        REV=${1:-head}
        poetry run alembic upgrade "$REV"
        ;;
    rollback|downgrade)
        REV=${1:--1}
        poetry run alembic downgrade "$REV"
        ;;
    merge)
        poetry run alembic merge heads
        ;;
    stamp)
        if [ $# -eq 0 ]; then
            echo "Error: stamp command requires a revision. Example: $0 stamp head"
            exit 1
        fi
        poetry run alembic stamp "$1"
        ;;
    history)
        poetry run alembic history --verbose
        ;;
    current)
        poetry run alembic current
        ;;
    shell|psql)
        docker exec -it backend_postgres psql -U postgres -d postgres
        ;;
    clean)
        read -p "Are you sure you want to drop the public schema? This will delete ALL data. (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            docker exec -it backend_postgres psql -U postgres -d postgres -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;"
            echo "Database cleaned."
        fi
        ;;
    reset)
        read -p "Are you sure you want to reset the database? This will delete ALL data and run migrations. (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            docker exec -it backend_postgres psql -U postgres -d postgres -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;"
            echo "Database cleaned. Running migrations..."
            poetry run alembic upgrade head
            echo "Database reset and migrations applied."
        fi
        ;;
    *)
        echo "Unknown command: $COMMAND"
        usage
        ;;
esac
