#!/usr/bin/env python3

import os
import re
import shlex
import sys
from pathlib import Path

# CB prep script configuration
WORKDIR = "."
CASA_EXECUTABLE = "casa-pipe"
CASA_SCRIPT = "casa_pipescript_666.py"
JOB_SCRIPT_NAME = "run_casa_pipescript.sh"
BATCH_LIST_FILE = "batch_files_list.txt"
DRY_RUN = False
APPEND_BATCH_LIST = False

SBATCH_TIME = "7-00:00:00"
SBATCH_MEM = "128GB"
SBATCH_NODES = 1
SBATCH_NTASKS_PER_NODE = 1
SBATCH_CPUS_PER_TASK = 1
SBATCH_PARTITION = ""
SBATCH_ACCOUNT = None
SBATCH_MAIL_TYPE = None
SBATCH_MAIL_USER = None
SBATCH_EXPORT_ALL = True
MODULE_LOAD = None
USE_EXECFILE = False


def load_slurm_mail_config(config_path=None):
    config_file = Path(config_path) if config_path is not None else Path(__file__).resolve().parents[1] / "slurm-mail.conf"
    if not config_file.exists():
        return None, None

    try:
        lines = config_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None

    mail_type = None
    mail_user = None
    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        key_lower = key.lower()
        if key_lower == "mail_type":
            mail_type = value
        elif key_lower == "mail_user":
            mail_user = value

    if not mail_type and not mail_user:
        return None, None

    allowed_types = {"NONE", "BEGIN", "END", "FAIL", "REQUEUE", "ALL", "TIME_LIMIT", "STAGE_OUT"}
    if mail_type:
        tokens = [token.strip().upper() for token in re.split(r"[\s,]+", mail_type) if token.strip()]
        if not tokens or any(token not in allowed_types for token in tokens):
            print(f"Warning: ignoring invalid Slurm mail_type in {config_file}; expected values like END,FAIL or FAIL", file=sys.stderr)
            return None, None
        mail_type = ",".join(tokens)
    if mail_user:
        email_pattern = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
        if not email_pattern.fullmatch(mail_user):
            print(f"Warning: ignoring invalid Slurm mail_user in {config_file}; expected a valid email address.", file=sys.stderr)
            return None, None

    return mail_type, mail_user


SBATCH_MAIL_TYPE, SBATCH_MAIL_USER = load_slurm_mail_config()

ENABLE_AUTO_IMAGE_FOLLOWUP = True
AUTO_IMAGE_DIR = "auto-image-VLA"
AUTO_IMAGE_SCRIPT = "run-auto-image.py"
AUTO_IMAGE_JOB_SCRIPT_NAME = "run_auto_image.sh"
AUTO_IMAGE_JOB_NAME_PREFIX = "AutoImage"
AUTO_IMAGE_USE_EXECFILE = False
AUTO_IMAGE_ENSURE_PANDAS = True


def build_slurm_script(
    workdir,
    job_name,
    output,
    error,
    time_limit,
    mem,
    nodes,
    ntasks_per_node,
    cpus_per_task,
    partition,
    account,
    mail_type,
    mail_user,
    export_all,
    module_load,
    commands,
    start_message,
    complete_message,
):
    workdir_abs = workdir.resolve()
    header_lines = ["#!/bin/bash", ""]

    if export_all:
        header_lines.append("#SBATCH --export=ALL")
    if account:
        header_lines.append(f"#SBATCH --account={account}")
    if partition:
        header_lines.append(f"#SBATCH --partition={partition}")

    header_lines.extend(
        [
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --output={output}",
            f"#SBATCH --error={error}",
            f"#SBATCH --chdir={shlex.quote(str(workdir_abs))}",
            f"#SBATCH --time={time_limit}",
            f"#SBATCH --mem={mem}",
            f"#SBATCH --nodes={nodes}",
            f"#SBATCH --ntasks-per-node={ntasks_per_node}",
            f"#SBATCH --cpus-per-task={cpus_per_task}",
        ]
    )

    if mail_type:
        header_lines.append(f"#SBATCH --mail-type={mail_type}")
    if mail_user:
        header_lines.append(f"#SBATCH --mail-user={mail_user}")

    body_lines = ["", "set -euo pipefail", "", f"echo \"{start_message}\"", ""]

    if module_load:
        body_lines.extend([f"echo \"Loading module: {module_load}\"", f"module load {module_load}", ""])

    for command in commands:
        safe_command = command.replace("'", "'\"'\"'")
        body_lines.append(f"echo 'Running: {safe_command}'")
        body_lines.append(command)
        body_lines.append("")
    body_lines.append(f"echo \"{complete_message}\"")

    return "\n".join(header_lines + body_lines) + "\n"


def build_casa_command(
    casa_executable,
    casa_script,
    use_execfile,
    ensure_user_site=False,
    preload_auto_image_tasks=False,
    print_traceback=False,
    run_in_script_dir=False,
):
    quoted_casa = shlex.quote(casa_executable)
    prelude = ""
    if ensure_user_site:
        prelude = (
            "import os,sys;"
            "_user_site=os.path.expanduser("
            "f'~/.local/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'"
            ");"
            "sys.path.insert(0,_user_site) if _user_site not in sys.path else None;"
        )
    if preload_auto_image_tasks:
        prelude += "from casatasks import listobs,tclean,imfit,imstat,imhead;"

    if use_execfile:
        exec_expr = f"execfile({repr(casa_script)})"
    else:
        # run_path preserves __file__ for scripts that use path-relative imports/resources.
        if run_in_script_dir:
            exec_expr = (
                f"import os,runpy;"
                f"_script={repr(casa_script)};"
                "_script_dir=os.path.dirname(_script) or '.';"
                "_script_name=os.path.basename(_script);"
                "os.chdir(_script_dir);"
                "runpy.run_path(_script_name, init_globals=globals(), run_name='__main__')"
            )
        else:
            exec_expr = f"import runpy;runpy.run_path({repr(casa_script)}, init_globals=globals(), run_name='__main__')"

    if print_traceback:
        python_code = (
            f"{prelude}import traceback;"
            "\ntry:\n"
            f"    {exec_expr}\n"
            "except Exception:\n"
            "    traceback.print_exc()\n"
            "    raise\n"
        )
    else:
        python_code = f"{prelude}{exec_expr}"
    return f"{quoted_casa} --nogui -c {shlex.quote(python_code)}"


def build_casa_install_pandas_command(casa_executable):
    quoted_casa = shlex.quote(casa_executable)
    python_code = (
        "import subprocess,sys;"
        "print(f'Installing pandas into CASA Python: {sys.executable}');"
        "subprocess.run([sys.executable,'-m','pip','install','--user','pandas'],check=True)"
    )
    return f"{quoted_casa} --nogui -c {shlex.quote(python_code)}"


def build_listobs_command(casa_executable, measurement_set, listfile):
    quoted_casa = shlex.quote(casa_executable)
    python_code = (
        "from casatasks import listobs;"
        f"listobs(vis={str(measurement_set)!r}, "
        f"listfile={str(listfile)!r}, overwrite=True)"
    )
    return f"{quoted_casa} --nogui -c {shlex.quote(python_code)}"


def build_final_ms_rename_command(target_name, project_code):
    target = shlex.quote(target_name)
    project_prefix = shlex.quote(f"{project_code}.")
    return "\n".join(
        [
            f"target_ms={target}",
            f"project_prefix={project_prefix}",
            'if [ -d "$target_ms" ]; then',
            '    echo "Final measurement set already exists: $target_ms"',
            '    exit 0',
            'fi',
            'source_ms=""',
            'for candidate in ./*.ms; do',
            '    candidate_name="${candidate#./}"',
            '    if [ -d "$candidate" ] && [[ "$candidate_name" == "$project_prefix"* ]]; then',
            '        source_ms="$candidate"',
            '        break',
            '    fi',
            'done',
            'if [ -z "$source_ms" ]; then',
            '    for candidate in ./*.ms; do',
            '        candidate_name="${candidate#./}"',
            '        if [ -d "$candidate" ] && [ "$candidate_name" != "calibrators.ms" ] && [ "$candidate_name" != "finalcalibrators.ms" ]; then',
            '            if [ -n "$source_ms" ]; then',
            '                echo "Error: multiple exported .ms directories found; refusing to rename automatically." >&2',
            '                echo "Candidates: $source_ms and $candidate" >&2',
            '                exit 1',
            '            fi',
            '            source_ms="$candidate"',
            '        fi',
            '    done',
            'fi',
            'if [ -z "$source_ms" ]; then',
            '    echo "Error: no final .ms directory found to rename." >&2',
            '    exit 1',
            'fi',
            'mv "$source_ms" "$target_ms"',
            'echo "Renamed final measurement set: $source_ms -> $target_ms"',
        ]
    )


def write_batch_list(batch_file, paths, append=False):
    batch_path = Path(batch_file).expanduser().resolve()
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    unique_paths = []
    seen = set()
    for script_path in paths:
        resolved = str(Path(script_path).expanduser().resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)

    if append and batch_path.exists():
        with batch_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                candidate = line.strip()
                if not candidate:
                    continue
                resolved = str(Path(candidate).expanduser().resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    unique_paths.append(resolved)

    mode = "w"
    with batch_path.open(mode, encoding="utf-8") as handle:
        for script_path in unique_paths:
            handle.write(script_path + "\n")


# generate SBATCH job script for the single CB workdir
workdir = Path(WORKDIR).expanduser().resolve()
if not workdir.exists() or not workdir.is_dir():
    print(f"Error: workdir does not exist or is not a directory: {workdir}", file=sys.stderr)
    sys.exit(1)

casa_script_path = workdir / CASA_SCRIPT
if not casa_script_path.exists():
    print(f"Error: CASA pipescript not found in {workdir}: {CASA_SCRIPT}", file=sys.stderr)
    sys.exit(1)

job_name = f"CalibrationPipeline-{workdir.name}"
# Use absolute paths for SLURM output/error to ensure logs are written on reruns
output = str(workdir / f"{job_name}.out")
error = str(workdir / f"{job_name}.err")
job_script_path = workdir / JOB_SCRIPT_NAME

calibration_command = build_casa_command(
    casa_executable=CASA_EXECUTABLE,
    casa_script=CASA_SCRIPT,
    use_execfile=USE_EXECFILE,
)
workdir_name = workdir.name
if workdir_name.startswith("CB."):
    final_ms_name = f"{workdir_name[3:]}.ms"
    project_code = workdir_name[3:].split(".", 1)[0]
else:
    final_ms_name = f"{workdir_name}.ms"
    project_code = workdir_name.split(".", 1)[0]
final_ms_rename_command = build_final_ms_rename_command(final_ms_name, project_code)
listobs_command = build_listobs_command(
    CASA_EXECUTABLE,
    workdir / final_ms_name,
    workdir / "listfile.txt",
)

script_content = build_slurm_script(
    workdir=workdir,
    job_name=job_name,
    output=output,
    error=error,
    time_limit=SBATCH_TIME,
    mem=SBATCH_MEM,
    nodes=SBATCH_NODES,
    ntasks_per_node=SBATCH_NTASKS_PER_NODE,
    cpus_per_task=SBATCH_CPUS_PER_TASK,
    partition=SBATCH_PARTITION,
    account=SBATCH_ACCOUNT,
    mail_type=SBATCH_MAIL_TYPE,
    mail_user=SBATCH_MAIL_USER,
    export_all=SBATCH_EXPORT_ALL,
    module_load=MODULE_LOAD,
    commands=[calibration_command, final_ms_rename_command, listobs_command],
    start_message="Starting CASA non-interactive calibration job",
    complete_message="CASA calibration job complete",
)

if DRY_RUN:
    print(f"# Generated SBATCH script for {workdir}")
    print(script_content)
    sys.exit(0)

job_script_path.write_text(script_content, encoding="utf-8")
job_script_path.chmod(0o755)
print(f"Created SLURM job script: {job_script_path}")

batch_paths = [str(job_script_path.resolve())]

if ENABLE_AUTO_IMAGE_FOLLOWUP:
    auto_image_script_path = workdir / AUTO_IMAGE_DIR / AUTO_IMAGE_SCRIPT
    if auto_image_script_path.exists():
        auto_job_name = f"{AUTO_IMAGE_JOB_NAME_PREFIX}-{workdir.name}"
        # Use absolute paths for SLURM output/error to ensure logs are written on reruns and dependency chains
        auto_output = str(workdir / f"{auto_job_name}.out")
        auto_error = str(workdir / f"{auto_job_name}.err")
        auto_job_script_path = workdir / AUTO_IMAGE_JOB_SCRIPT_NAME
        auto_image_command = build_casa_command(
            casa_executable=CASA_EXECUTABLE,
            casa_script=f"{AUTO_IMAGE_DIR}/{AUTO_IMAGE_SCRIPT}",
            use_execfile=AUTO_IMAGE_USE_EXECFILE,
            ensure_user_site=AUTO_IMAGE_ENSURE_PANDAS,
            preload_auto_image_tasks=True,
            print_traceback=True,
            run_in_script_dir=True,
        )
        auto_commands = [auto_image_command]
        if AUTO_IMAGE_ENSURE_PANDAS:
            auto_commands.insert(0, build_casa_install_pandas_command(CASA_EXECUTABLE))
        auto_script_content = build_slurm_script(
            workdir=workdir,
            job_name=auto_job_name,
            output=auto_output,
            error=auto_error,
            time_limit=SBATCH_TIME,
            mem=SBATCH_MEM,
            nodes=SBATCH_NODES,
            ntasks_per_node=SBATCH_NTASKS_PER_NODE,
            cpus_per_task=SBATCH_CPUS_PER_TASK,
            partition=SBATCH_PARTITION,
            account=SBATCH_ACCOUNT,
            mail_type=SBATCH_MAIL_TYPE,
            mail_user=SBATCH_MAIL_USER,
            export_all=SBATCH_EXPORT_ALL,
            module_load=MODULE_LOAD,
            commands=auto_commands,
            start_message="Starting auto-image follow-up job",
            complete_message="Auto-image job complete",
        )
        auto_job_script_path.write_text(auto_script_content, encoding="utf-8")
        auto_job_script_path.chmod(0o755)
        print(f"Created follow-up SLURM job script: {auto_job_script_path}")
        batch_paths.append(str(auto_job_script_path.resolve()))
    else:
        print(
            f"Warning: auto-image follow-up script not found at {auto_image_script_path}; "
            "second job was not generated."
        )

write_batch_list(BATCH_LIST_FILE, batch_paths, append=APPEND_BATCH_LIST)
action = "Appended" if APPEND_BATCH_LIST else "Wrote"
print(f"{action} {len(batch_paths)} script path(s) to {Path(BATCH_LIST_FILE).expanduser().resolve()}")
