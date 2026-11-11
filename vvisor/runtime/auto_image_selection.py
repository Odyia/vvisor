import re
import textwrap
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


__all__ = [
    "band_for_frequency",
    "build_image_options",
    "prompt_for_image_options",
]


_BAND_RANGES_GHZ = (
    (1.0, 2.0, "EVLA_L"),
    (2.0, 4.0, "EVLA_S"),
    (4.0, 8.0, "EVLA_C"),
    (8.0, 12.0, "EVLA_X"),
    (12.0, 18.0, "EVLA_Ku"),
    (18.0, 26.5, "EVLA_K"),
    (26.5, 40.0, "EVLA_Ka"),
    (40.0, 50.0, "EVLA_Q"),
)


def band_for_frequency(frequency_hz: float, name: str = "") -> Optional[str]:
    match = re.match(r"(EVLA_(?:L|S|C|X|Ku|K|Ka|Q))#", name, re.IGNORECASE)
    if match:
        canonical_bands = {
            "evla_l": "EVLA_L",
            "evla_s": "EVLA_S",
            "evla_c": "EVLA_C",
            "evla_x": "EVLA_X",
            "evla_ku": "EVLA_Ku",
            "evla_k": "EVLA_K",
            "evla_ka": "EVLA_Ka",
            "evla_q": "EVLA_Q",
        }
        return canonical_bands[match.group(1).lower()]

    frequency_ghz = frequency_hz / 1e9
    for lower, upper, band in _BAND_RANGES_GHZ:
        if lower <= frequency_ghz < upper:
            return band
    return None


def build_image_options(
    spectral_windows: Iterable[Tuple[int, str, float]],
    split: str,
    use_single_band: bool = False,
    single_band: Optional[str] = None,
    target_spw_ids: Optional[Iterable[int]] = None,
) -> List[dict]:
    windows = list(spectral_windows)
    if split not in {"whole", "halves", "both"}:
        raise ValueError(f"Unsupported auto-image split value: {split}")

    bands: Dict[str, List[Tuple[int, str, float]]] = {}
    target_ids = None if target_spw_ids is None else {int(spw_id) for spw_id in target_spw_ids}
    windows_to_use = (
        windows
        if target_ids is None
        else [window for window in windows if int(window[0]) in target_ids]
    )
    for spw_id, name, frequency_hz in windows_to_use:
        band = band_for_frequency(frequency_hz, name)
        if use_single_band and band != single_band:
            continue
        if band:
            bands.setdefault(band, []).append((spw_id, name, frequency_hz / 1e9))

    options = []
    for band, band_windows in bands.items():
        band_windows.sort(key=lambda window: (window[2], window[0]))
        count = len(band_windows)
        lower = band_windows[: count // 2]
        upper = band_windows[count // 2 :]
        groups = []
        if split in {"halves", "both"}:
            groups.extend((("lower", lower), ("upper", upper)))
        if split in {"whole", "both"}:
            groups.append(("all", band_windows))

        for group_name, group_windows in groups:
            if not group_windows:
                continue
            spw_ids = [window[0] for window in group_windows]
            frequencies = [window[2] for window in group_windows]
            options.append(
                {
                    "band": band,
                    "split": group_name,
                    "frequency_ghz": round(sum(frequencies) / len(frequencies), 2),
                    "spws": ",".join(str(spw_id) for spw_id in sorted(spw_ids)),
                    "spw_details": [
                        {
                            "id": spw_id,
                            "name": name,
                            "frequency_ghz": round(frequency_ghz, 4),
                            "band": band,
                        }
                        for spw_id, name, frequency_ghz in group_windows
                    ],
                }
            )

    return sorted(options, key=lambda option: (option["frequency_ghz"], option["band"], option["split"]))


def _prompt_for_manual_image_options(options: Sequence[dict]) -> List[dict]:
    available_spws = {}
    for option in options:
        for spw in option.get("spw_details", []):
            available_spws[spw["id"]] = spw

    if not available_spws:
        raise RuntimeError("No spectral-window details are available for manual image selection.")

    print("\nSPW details (ID | center GHz | band | name):")
    for spw_id in sorted(available_spws):
        spw = available_spws[spw_id]
        detail = (
            f"{spw_id:>4} | {spw['frequency_ghz']:>10.4f} | "
            f"{spw['band']:<7} | {spw['name'] or 'unnamed'}"
        )
        wrapped = textwrap.wrap(
            detail,
            width=76,
            subsequent_indent="                         ",
            break_long_words=True,
            break_on_hyphens=False,
        )
        print("\n".join(wrapped))

    print("Manual mode: enter a comma-separated SPW ID list for each image.")
    selected = []
    while True:
        try:
            response = input(
                f"SPWs for image {len(selected) + 1} (comma-separated, or 'done' to finish): "
            ).strip()
        except EOFError as exc:
            raise RuntimeError(
                "Auto-image product selection needs an interactive terminal; rerun without "
                "--auto-image-select or provide selections in config.yaml."
            ) from exc

        if response.lower() == "done":
            if selected:
                return selected
            print("Enter at least one SPW group before finishing.")
            continue

        try:
            spw_ids = [int(value.strip()) for value in response.split(",")]
        except ValueError:
            spw_ids = []

        if not spw_ids or len(spw_ids) != len(set(spw_ids)):
            print("Enter one or more distinct SPW IDs separated by commas.")
            continue
        if any(spw_id not in available_spws for spw_id in spw_ids):
            print(
                "Unknown SPW ID. Choose from: "
                + ", ".join(str(spw_id) for spw_id in sorted(available_spws))
                + "."
            )
            continue

        spws = [available_spws[spw_id] for spw_id in spw_ids]
        bands = {spw["band"] for spw in spws}
        if len(bands) != 1:
            print("Each image group must contain SPWs from only one VLA band.")
            continue

        band = next(iter(bands))
        selected.append(
            {
                "band": band,
                "split": f"manual-{len(selected) + 1}",
                "frequency_ghz": round(
                    sum(spw["frequency_ghz"] for spw in spws) / len(spws), 2
                ),
                "spws": ",".join(str(spw_id) for spw_id in sorted(spw_ids)),
                "spw_details": sorted(spws, key=lambda spw: spw["id"]),
                "manual": True,
            }
        )


def prompt_for_image_options(options: Sequence[dict]) -> List[dict]:
    if not options:
        raise RuntimeError("No supported VLA bands or frequency groups were found in the dataset metadata.")

    print("Available image groups:")
    for index, option in enumerate(options, start=1):
        split_label = {"lower": "lower half", "upper": "upper half", "all": "whole band"}[
            option["split"]
        ]
        print(
            f"  {index:>2}. {option['band']:<7} {split_label:<10} "
            f"{option['frequency_ghz']:>6.2f} GHz"
        )
        spw_ids = option.get("spws", "")
        print(
            textwrap.fill(
                f"SPWs: {spw_ids}",
                width=76,
                initial_indent="      ",
                subsequent_indent="            ",
            )
        )

    while True:
        try:
            response = input(
                "Select products by number (comma-separated), 'all', or 'manual': "
            ).strip()
        except EOFError as exc:
            raise RuntimeError(
                "Auto-image product selection needs an interactive terminal; rerun without "
                "--auto-image-select or provide selections in config.yaml."
            ) from exc

        if response.lower() == "all":
            return list(options)
        if response.lower() in {"m", "manual"}:
            return _prompt_for_manual_image_options(options)

        try:
            indices = [int(value.strip()) for value in response.split(",")]
        except ValueError:
            indices = []

        if indices and all(1 <= index <= len(options) for index in indices):
            selected = []
            seen = set()
            for index in indices:
                option = options[index - 1]
                key = (option["band"], option["split"], option["frequency_ghz"])
                if key not in seen:
                    selected.append(option)
                    seen.add(key)
            return selected

        print(f"Enter 'all', 'manual', or numbers from 1 to {len(options)}.")
