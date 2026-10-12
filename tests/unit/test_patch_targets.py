"""T2 producer admission falsifiers; no ROM or emulator needed."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("companion_build", ROOT / "patch/tools/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.mark.parametrize("title", ["firered", "leafgreen", "emerald"])
@pytest.mark.parametrize("version", [None, "dev", "v0.3.0", "v0.3.0-dev"])
def test_candidate_cli_forwards_menu_version(monkeypatch, title, version):
    producer = Mock()
    monkeypatch.setattr(build, "build_arena_probe", producer)
    args = ["build.py", "--trade-candidate", "--target", title, "--rom", "input.gba"]
    if version is not None:
        args += ["--version", version]
    monkeypatch.setattr(sys, "argv", args)
    assert build.main() == 0
    producer.assert_called_once_with(
        title, "input.gba", "trade", trade_candidate=True, version=version,
    )


def test_candidate_cli_rejects_bad_version_before_build(monkeypatch, capsys):
    producer = Mock()
    monkeypatch.setattr(build, "build_arena_probe", producer)
    monkeypatch.setattr(sys, "argv", [
        "build.py", "--trade-candidate", "--target", "firered", "--version", "invalid",
    ])
    with pytest.raises(SystemExit) as error:
        build.main()
    assert error.value.code == 2
    assert "version must be" in capsys.readouterr().err
    producer.assert_not_called()


def test_firered_refuses_rr_mailbox_allocator_overlap():
    with pytest.raises(ValueError, match="overlap.*__malloc_av_"):
        build.validate_arena("firered", 0x0203F800, 0x800)


def test_zero_sized_linker_heap_symbol_does_not_mean_free_ram():
    with pytest.raises(ValueError, match="overlap.*gHeap"):
        build.validate_arena("firered", 0x0201B000, 0x1000)


def test_wrong_base_and_detour_refused_before_compile():
    with pytest.raises(ValueError, match="base ROM"):
        build.validate_base("firered", bytes(0x1000000))
    with pytest.raises(ValueError, match="detour"):
        build.validate_detour(bytes(0x1000), 0x0800051A, bytes.fromhex("3bf1a9f9"))


def test_far_heap_entry_is_a_full_thumb_veneer_not_out_of_range_bl():
    assert build.thumb_entry_jump(0x08002B80, 0x08EB0B20) == bytes.fromhex("004b1847210beb08")
    with pytest.raises(ValueError, match="aligned"):
        build.thumb_entry_jump(0x08002B82, 0x08EB0B20)


def test_published_targets_reserve_heap_and_unqualified_rr_v2_stays_closed():
    for target in ("firered", "leafgreen", "emerald"):
        assert build.require_ready(target)["ARENA_BASE"]==0x0201B000
    with pytest.raises(ValueError, match="not qualified"):
        build.require_ready("radical_red")


def test_abi_success_requires_native_post_save(tmp_path):
    gcc = os.environ.get("SLINK_HOST_GCC") or shutil.which("gcc")
    if not gcc:
        pytest.skip("host C compiler absent; set SLINK_HOST_GCC")
    source = tmp_path / "abi.c"
    source.write_text('''#include "abi.h"
_Static_assert(SLINK_REASON_UNCERTAIN == 11, "v2 uncertain reason");
_Static_assert(SLINK_REASON_IDENTITY == 12, "v2 identity reason");
_Static_assert(SLINK_REASON_CLIENT_TOO_OLD == 13, "v2 unarmed client reason");
int main(void) {
    SlinkTradeWitnessV2 w = {0};
    w.received_pid = 33; w.received_otid = 44;
    w.visit_flags = SLINK_VISIT_ACCEPTED | SLINK_PRE_SAVE_CONSENT;
    w.final_result = SLINK_TRADE_COMMITTED;
    w.save_status = SLINK_SAVE_OK;
    w.milestone_seq[SLINK_PRE_SAVE_OK] = 7;
    for (unsigned i = 1; i < 5; i++) w.milestone_seq[i] = 9;
    w.milestones = (1u << SLINK_PRE_SAVE_OK) | (1u << SLINK_COMMIT_ENTERED)
                 | (1u << SLINK_SCENE_EVOLUTION_DONE);
    if (slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 1;
    w.milestones |= (1u << SLINK_POST_SAVE_OK);
    if (slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 2;
    w.milestones |= (1u << SLINK_FINAL_RESULT);
    if (!slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 3;
    w.received_pid = 34;
    if (slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 6;
    w.received_pid = 33; w.received_otid = 45;
    if (slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 7;
    w.received_otid = 44;
    w.milestone_seq[SLINK_FINAL_RESULT] = 8;
    if (slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 4;
    w.milestone_seq[SLINK_FINAL_RESULT] = 9;
    w.save_status = SLINK_SAVE_FAILED;
    if (slink_trade_success_is_durable(&w, 7, 9, 33, 44)) return 5;
    return 0;
}
''')
    exe = tmp_path / "abi.exe"
    compiled = subprocess.run([gcc, "-std=c11", "-Wall", "-Werror", "-I",
                               str(ROOT / "patch/src/trade_targets"), str(source), "-o", str(exe)],
                              capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    assert subprocess.run([str(exe)], timeout=5).returncode == 0


def test_full_name_requires_both_source_and_destination_bounds(tmp_path):
    gcc = os.environ.get("SLINK_HOST_GCC") or shutil.which("gcc")
    if not gcc:
        pytest.skip("host C compiler absent; set SLINK_HOST_GCC")
    source = tmp_path / "name.c"
    source.write_text('''#include "abi.h"
/* Negative control models the removed until-EOS copy, using a physically large
 * backing array so the deliberate logical-capacity overrun is not host UB. */
static void old_copy(uint8_t *d, const uint8_t *s) {
    do { *d++ = *s; } while (*s++ != 0xFF);
}
int main(void) {
    uint8_t src[32], out[48];
    for (unsigned i=0;i<32;i++) src[i]=0xBB;
    src[31]=0xFF;
    for (unsigned i=0;i<48;i++) out[i]=0xCC;
    old_copy(out,src);
    if (out[20]==0xCC) return 1;
    for (unsigned i=0;i<48;i++) out[i]=0xCC;
    slink_copy_name_bounded(out,20,src,10);
    if (out[9]!=0xBB || out[10]!=0xFF || out[20]!=0xCC) return 2;
    slink_copy_name_bounded(out,8,src,10);
    if (out[6]!=0xBB || out[7]!=0xFF) return 3;
    src[2]=0xFF;
    slink_copy_name_bounded(out,20,src,10);
    if (out[2]!=0xFF) return 4;
    return 0;
}
''')
    exe = tmp_path / "name.exe"
    compiled = subprocess.run([gcc, "-std=c11", "-Wall", "-Werror", "-I",
                               str(ROOT / "patch/src/trade_targets"), str(source), "-o", str(exe)],
                              capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    assert subprocess.run([str(exe)], timeout=5).returncode == 0
