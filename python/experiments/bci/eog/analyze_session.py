"""
analyze_session.py — Post-session analysis for Flappy Blink data
=================================================================
Generates three separate plot windows from a recorded session CSV:

  1. Bird y position + EEG blink square wave overlay
  2. Confusion Matrix — EEG detections vs CV ground truth
  3. ROC Curve    — EEG amplitude score vs CV binary labels

Only analyzes frames where game_state == 'playing' and score >= 0.

Usage
-----
    python analyze_session.py latest
    python analyze_session.py ../sessions/session_20260408_141500.csv
    python analyze_session.py latest --match 400 --save

Requirements
------------
    pip install matplotlib pandas numpy
"""

import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.widgets import RangeSlider
from pathlib import Path

SESSIONS_DIR = Path(__file__).parent.parent / 'sessions'

C_BIRD       = '#2060CC'
C_BLINK_WAVE = '#E03030'
C_CV         = '#FF8C00'


# ── helpers ───────────────────────────────────────────────────────────────────

def _event_match(df, match_s):
    """
    Event-based blink matching — the right metric for infrequent events.

    For each CV blink, check if there is an EEG blink within ±match_s.
    For each EEG blink, check if there is a CV blink within ±match_s.

    Returns (TP, FP, FN):
      TP — EEG blink matched to a CV blink
      FP — EEG blink with no CV blink nearby
      FN — CV blink with no EEG blink nearby

    TN is undefined for event-based matching (you can't count the infinite
    moments where neither detector fired).
    """
    t       = df['time_s'].values
    cv_t    = t[df['cv_blink'].fillna(0).values > 0]
    eeg_t   = t[df['eeg_blink'].values > 0]

    # greedy match: each CV event consumed at most once
    cv_matched  = np.zeros(len(cv_t),  dtype=bool)
    eeg_matched = np.zeros(len(eeg_t), dtype=bool)

    for ei, et in enumerate(eeg_t):
        diffs = np.abs(cv_t - et)
        if len(diffs) == 0:
            continue
        best = np.argmin(diffs)
        if diffs[best] <= match_s and not cv_matched[best]:
            eeg_matched[ei] = True
            cv_matched[best] = True

    TP = int(eeg_matched.sum())
    FP = int((~eeg_matched).sum())
    FN = int((~cv_matched).sum())
    return TP, FP, FN


def _event_roc_scores(df, match_s):
    """
    Event-aligned ROC scores — avoids the EEG-leads-CV timing problem.

    Positive: for each CV blink at time ct, score = max |EEG amplitude| in
              [ct - match_s, ct + match_s * 0.25]  (mostly backward, since EEG fires first)
    Negative: for windows whose centre is > match_s from any CV blink,
              score = max |EEG amplitude| in that window.

    Returns (scores, gt) where gt=1 for positive examples, gt=0 for negatives.
    """
    t    = df['time_s'].values
    amp  = df['eeg_amplitude_uv'].values
    cv_t = t[df['cv_blink'].fillna(0).values > 0]

    pos_scores = []
    for ct in cv_t:
        mask = (t >= ct - match_s) & (t <= ct + match_s * 0.25)
        pos_scores.append(float(np.abs(amp[mask]).max()) if mask.sum() > 0 else 0.0)

    step = match_s / 2   # non-overlapping negative windows
    neg_scores = []
    for t_center in np.arange(match_s, t.max() - match_s / 2, step):
        if len(cv_t) > 0 and np.min(np.abs(cv_t - t_center)) < match_s:
            continue   # too close to a real blink
        mask = (t >= t_center - step / 2) & (t < t_center + step / 2)
        if mask.sum() > 0:
            neg_scores.append(float(np.abs(amp[mask]).max()))

    scores = np.concatenate([np.array(pos_scores), np.array(neg_scores)])
    gt     = np.concatenate([np.ones(len(pos_scores)), np.zeros(len(neg_scores))])
    return scores, gt


def _has_cv(df):
    return ('cv_blink' in df.columns
            and df['cv_blink'].notna().any()
            and df['cv_blink'].sum() > 0)


def _make_blink_squarewave(df, hold_s=0.25):
    """
    Step function: 1.0 for hold_s after each eeg_blink event, else 0.
    Produces a clean square wave for overlay on the bird y plot.
    """
    t           = df['time_s'].values
    sq          = np.zeros(len(t))
    blink_times = df.loc[df['eeg_blink'] == 1, 'time_s'].values
    for bt in blink_times:
        mask = (t >= bt) & (t <= bt + hold_s)
        sq[mask] = 1.0
    return sq


# ── plot 1: bird y + EEG blink square wave ───────────────────────────────────

def plot_bird_blinks(df, stem=''):
    fig, ax_bird = plt.subplots(figsize=(14, 5))
    fig.subplots_adjust(bottom=0.20)   # room for the time slider

    t      = df['time_s'].values
    bird_y = df['bird_y'].values

    # Scale the blink square wave into bird_y pixel coordinates so it sits
    # on the same axis as the bird and the amplitude lines up visually.
    # sq=0 → at_rest (bird's typical floating level, high y = visual bottom)
    # sq=1 → jump_peak (bird's typical apex after a blink, low y = visual top)
    sq        = _make_blink_squarewave(df)
    at_rest   = float(np.percentile(bird_y, 80))   # high pygame y = low on screen
    jump_peak = float(np.percentile(bird_y, 10))   # low pygame y = high on screen
    sq_scaled = at_rest - sq * (at_rest - jump_peak)

    ax_bird.plot(t, bird_y, color=C_BIRD, lw=1.4, label='Bird y', zorder=2)
    ax_bird.fill_between(t, at_rest, sq_scaled, step='post', alpha=0.25,
                         color=C_BLINK_WAVE, label='EEG blink', zorder=3)
    ax_bird.plot(t, sq_scaled, color=C_BLINK_WAVE, lw=1.1, alpha=0.85,
                 drawstyle='steps-post', zorder=4)

    ax_bird.invert_yaxis()   # pygame y increases downward; invert for intuitive view
    ax_bird.set_ylabel('Bird y (px)')
    ax_bird.set_xlabel('Time (s)')
    ax_bird.grid(True, alpha=0.25)
    ax_bird.legend(loc='upper left', fontsize=9)

    # Time range slider — drag handles to zoom into a sub-interval
    t_min, t_max = float(t.min()), float(t.max())
    ax_slider = fig.add_axes([0.10, 0.06, 0.80, 0.04])
    slider = RangeSlider(ax_slider, 'Time (s)', t_min, t_max,
                         valinit=(t_min, t_max))

    def _on_slider(val):
        ax_bird.set_xlim(slider.val)
        fig.canvas.draw_idle()

    slider.on_changed(_on_slider)
    fig._time_slider = slider   # keep reference so it isn't garbage-collected

    fig.suptitle(f'Flappy Blink — {stem}', fontsize=11, fontweight='bold')
    return fig


# ── plot 2: confusion matrix ──────────────────────────────────────────────────

def plot_confusion_matrix(df, match_ms=400, stem=''):
    """
    Event-based confusion matrix. TN cell shows N/A — it is undefined for
    event-based matching since you can't count the infinite non-blink moments.
    """
    fig, ax = plt.subplots(figsize=(5, 4.5))

    if not _has_cv(df):
        ax.text(0.5, 0.5, 'No CV ground truth\nin this session',
                ha='center', va='center', transform=ax.transAxes,
                color='#808080', fontsize=12)
        ax.set_title('Confusion Matrix')
        fig.suptitle(f'Flappy Blink — {stem}', fontsize=11, fontweight='bold')
        fig.tight_layout()
        return fig

    TP, FP, FN = _event_match(df, match_s=match_ms / 1000.0)

    # 2×2 layout: [[TP, FN], [FP, N/A]]
    cm = np.array([[TP, FN],
                   [FP,  0]], dtype=float)

    ax.imshow(cm, cmap='Blues', vmin=0)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(['Blink\n(CV truth)', 'No Blink\n(CV truth)'])
    ax.set_yticks([0, 1])
    ax.set_yticklabels(['Blink\n(EOG)', 'No Blink\n(EOG)'])

    cell_labels = [[str(TP), str(FN)], [str(FP), 'N/A']]
    for i in range(2):
        for j in range(2):
            val = cm[i, j]
            col = 'white' if val > cm.max() * 0.55 else 'black'
            ax.text(j, i, cell_labels[i][j], ha='center', va='center',
                    color=col, fontsize=15, fontweight='bold')

    prec = TP / (TP + FP) if (TP + FP) else 0
    rec  = TP / (TP + FN) if (TP + FN) else 0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    ax.set_title('Confusion Matrix')
    ax.set_xlabel(
        f'Prec={prec:.2f}  Rec={rec:.2f}  F1={f1:.2f}',
        fontsize=9)

    fig.suptitle(f'Flappy Blink — {stem}', fontsize=11, fontweight='bold')
    fig.tight_layout()
    return fig


# ── plot 3: ROC curve ─────────────────────────────────────────────────────────

def plot_roc(df, match_ms=400, stem=''):
    """
    ROC curve using event-aligned scoring.
    Positive score = max |EEG amplitude| in the lookback window before each CV blink.
    Negative score = max |EEG amplitude| in windows far from any blink.
    Same match_ms as the confusion matrix.
    """
    fig, ax = plt.subplots(figsize=(5, 4.5))

    if not _has_cv(df):
        ax.text(0.5, 0.5, 'No CV ground truth\nin this session',
                ha='center', va='center', transform=ax.transAxes,
                color='#808080', fontsize=12)
        ax.set_title('ROC Curve')
        fig.suptitle(f'Flappy Blink — {stem}', fontsize=11, fontweight='bold')
        fig.tight_layout()
        return fig

    scores, gt = _event_roc_scores(df, match_s=match_ms / 1000.0)

    if gt.sum() == 0 or (1 - gt).sum() == 0:
        ax.text(0.5, 0.5, 'Not enough blink events\nfor ROC',
                ha='center', va='center', transform=ax.transAxes,
                color='#808080', fontsize=12)
        ax.set_title('ROC Curve')
        fig.suptitle(f'Flappy Blink — {stem}', fontsize=11, fontweight='bold')
        fig.tight_layout()
        return fig

    pos, neg = int(gt.sum()), int((1 - gt).sum())
    thresholds = np.unique(scores)[::-1]
    tprs = [0.0]
    fprs = [0.0]

    for thr in thresholds:
        pred = (scores >= thr).astype(int)
        tprs.append(((pred == 1) & (gt == 1)).sum() / pos)
        fprs.append(((pred == 1) & (gt == 0)).sum() / neg)

    tprs.append(1.0); fprs.append(1.0)
    auc = float(np.trapz(tprs, fprs))

    ax.plot(fprs, tprs, color='#2060CC', lw=2, label=f'EOG = {auc:.3f}')
    ax.plot([0, 1], [0, 1], '--', color='#888888', lw=1, label='Random')
    ax.fill_between(fprs, tprs, alpha=0.10, color='#2060CC')

    ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    ax.set_xlabel('False Positive Rate')
    ax.set_ylabel('True Positive Rate')
    ax.set_title(f'ROC Curve')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.25)

    fig.suptitle(f'Flappy Blink — {stem}', fontsize=11, fontweight='bold')
    fig.tight_layout()
    return fig


# ── summary stats ─────────────────────────────────────────────────────────────

def print_summary(df):
    duration   = df['time_s'].max()
    n_eeg      = int(df['eeg_blink'].sum())
    max_score  = int(df['score'].max())
    print(f"\n{'─'*50}")
    print(f"  Duration      : {duration:.1f} s")
    print(f"  EEG blinks    : {n_eeg}  ({n_eeg/duration*60:.1f}/min)")
    print(f"  Best score    : {max_score} pipes")
    if _has_cv(df):
        n_cv = int(df['cv_blink'].sum())
        print(f"  CV blinks     : {n_cv}  ({n_cv/duration*60:.1f}/min)")
    print(f"{'─'*50}\n")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Analyze Flappy Blink session data')
    parser.add_argument('session',
                        help='Path to session CSV, or "latest" to auto-pick')
    parser.add_argument('--match', type=int, default=400,
                        help='±ms match window used for both confusion matrix and ROC (default 400)')
    parser.add_argument('--save', action='store_true',
                        help='Save PNGs next to the CSV instead of showing')
    args = parser.parse_args()

    if args.session == 'latest':
        csvs = sorted(SESSIONS_DIR.glob('session_*.csv'))
        if not csvs:
            print(f'No sessions found in {SESSIONS_DIR}')
            sys.exit(1)
        path = csvs[-1]
    else:
        path = Path(args.session)

    if not path.exists():
        print(f'File not found: {path}')
        sys.exit(1)

    print(f'Loading: {path}')
    df = pd.read_csv(path)

    # Only analyze playing frames with valid score — discard waiting/dead
    df = df[(df['game_state'] == 'playing') & (df['score'] >= 0)].reset_index(drop=True)
    if len(df) == 0:
        print('No playing frames found in session.')
        sys.exit(1)

    # Re-zero time from start of first playing frame
    df['time_s'] = df['time_s'] - df['time_s'].iloc[0]

    print(f'  {len(df)} playing frames, {df["time_s"].max():.1f} s, '
          f'{int(df["score"].max())} pipes cleared')
    print_summary(df)

    stem = path.stem
    fig1 = plot_bird_blinks(df, stem=stem)
    fig2 = plot_confusion_matrix(df, match_ms=args.match, stem=stem)
    fig3 = plot_roc(df, match_ms=args.match, stem=stem)

    if args.save:
        fig1.savefig(path.with_name(stem + '_bird.png'),  dpi=150, bbox_inches='tight')
        fig2.savefig(path.with_name(stem + '_cm.png'),    dpi=150, bbox_inches='tight')
        fig3.savefig(path.with_name(stem + '_roc.png'),   dpi=150, bbox_inches='tight')
        print(f'Saved: {stem}_bird.png, {stem}_cm.png, {stem}_roc.png')
    else:
        plt.show()


if __name__ == '__main__':
    main()
