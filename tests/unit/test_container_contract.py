from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
CALC_BUILD = (ROOT / "calc" / "build").read_text(encoding="utf-8")


def test_container_runs_manager_as_non_root_with_init():
    assert "USER 10001:10001" in DOCKERFILE
    assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in DOCKERFILE
    assert '"-m", "server.manager"' in DOCKERFILE


def test_container_exposes_manager_and_five_game_ports():
    assert "EXPOSE 8090 54321 54322 54323 54324 54325" in DOCKERFILE
    assert "/healthz" in DOCKERFILE


def test_container_persists_runs_and_uploaded_roms_under_data():
    assert "SLINK_DATA_DIR=/data/runs" in DOCKERFILE
    assert "SLINK_ROM_DIR=/data/roms" in DOCKERFILE
    assert 'VOLUME ["/data"]' in DOCKERFILE


def test_container_builds_and_copies_damage_calculator():
    assert "FROM node:22-bookworm-slim AS calc-builder" in DOCKERFILE
    assert "npm ci --ignore-scripts" in DOCKERFILE
    assert "npm ci --prefix calc --ignore-scripts" in DOCKERFILE
    assert "RUN npm run build" in DOCKERFILE
    assert "COPY --from=calc-builder --chown=slink:slink /calc/dist ./calc/dist" in DOCKERFILE


def test_calculator_compile_uses_its_locked_typescript():
    package = (ROOT / "calc" / "calc" / "package.json").read_text(encoding="utf-8")
    assert "node ./node_modules/typescript/bin/tsc -p ." in package


def test_calculator_asset_copy_is_portable_and_skips_generated_scripts():
    assert "path.normalize(src) === path.normalize('src/js/data/sets')" in CALC_BUILD
    assert "/^(?:gen\\d+|normal|hardcore)\\.js$/" in CALC_BUILD
    assert "contents.lastIndexOf('};')" in CALC_BUILD


def test_build_context_excludes_private_game_files_and_runtime_state():
    for pattern in ("*.sav", "*.SaveRAM", "*.gba", "*.nds", "roms", "data/runs"):
        assert pattern in DOCKERIGNORE.splitlines()
