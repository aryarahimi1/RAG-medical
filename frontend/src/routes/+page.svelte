<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { applyTheme, resolveInitialTheme, type Theme } from '$lib/theme';

	type CorpusStats = {
		n_chunks: number;
		n_drugs: number;
		n_sources: number;
	};

	let theme = $state<Theme>('light');
	let corpus = $state<CorpusStats | null>(null);

	onMount(() => {
		if (!browser) return;
		theme = resolveInitialTheme();
		applyTheme(theme);

		// Corpus stats are decorative on this page — omit silently on failure.
		const controller = new AbortController();
		fetch('/api/corpus/stats', { signal: controller.signal })
			.then((r) => (r.ok ? r.json() : null))
			.then((data) => {
				if (data && typeof data.n_chunks === 'number') corpus = data as CorpusStats;
			})
			.catch(() => {
				/* offline / API down — show nothing */
			});

		return () => controller.abort();
	});

	function toggleTheme() {
		theme = theme === 'light' ? 'dark' : 'light';
		applyTheme(theme);
	}

	const corpusLine = $derived(
		corpus
			? `${corpus.n_chunks.toLocaleString()} chunks indexed across ${corpus.n_drugs.toLocaleString()} medications from ${corpus.n_sources.toLocaleString()} source feeds.`
			: ''
	);
</script>

<svelte:head>
	<title>Medication Reference</title>
	<meta
		name="description"
		content="A grounded reference desk for medication interactions, indications, and FDA recalls. Answers cite FDA DailyMed, NIH MedlinePlus, and OpenFDA enforcement records."
	/>
	<meta name="theme-color" content={theme === 'dark' ? '#1f1d24' : '#f8f6f0'} />
</svelte:head>

<a class="skip-link" href="#begin">Skip to the consultation</a>

<div class="page">
	<header class="masthead">
		<a class="brand" href="/" aria-label="Medication Reference home">
			<span class="brand-mark" aria-hidden="true">
				<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
					<text
						x="12"
						y="16.5"
						text-anchor="middle"
						fill="currentColor"
						font-size="14"
						font-family="Georgia, 'Times New Roman', 'Noto Serif', serif"
						font-weight="600">℞</text
					>
				</svg>
			</span>
			<span class="brand-wordmark">Medication Reference</span>
		</a>

		<button
			type="button"
			class="icon-btn"
			onclick={toggleTheme}
			aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
			title={theme === 'light' ? 'Dark mode' : 'Light mode'}
		>
			{#if theme === 'light'}
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
					<path d="M20 13.5A8 8 0 0 1 10.5 4a8 8 0 1 0 9.5 9.5z" stroke-linecap="round" stroke-linejoin="round" />
				</svg>
			{:else}
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
					<circle cx="12" cy="12" r="4" />
					<path
						d="M12 3v2M12 19v2M5 12H3M21 12h-2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"
						stroke-linecap="round"
					/>
				</svg>
			{/if}
		</button>
	</header>

	<main class="document">
		<section class="hero">
			<p class="section-no">§ 00 — Reference desk</p>
			<h1 class="opening">
				A grounded reference desk for medication interactions, indications, and FDA recalls.
			</h1>
			<p class="body">
				Answers cite FDA DailyMed labels, NIH MedlinePlus monographs, and OpenFDA enforcement
				records. Personal details are redacted before retrieval. This is an educational reference,
				not medical advice.
			</p>

			<div class="cta-row">
				<a id="begin" class="cta" href="/chat">
					<span>Begin a consultation</span>
					<span class="cta-cue" aria-hidden="true">→</span>
				</a>
				<span class="cta-meta">Enter to send. Shift+Enter for newline. No account required.</span>
			</div>
		</section>

		<hr class="rule" />

		<section class="manifest" aria-label="What this reference does and does not do">
			<article>
				<p class="eyebrow">§ 01 — What it answers</p>
				<p>
					Indications, contraindications, drug-drug interactions, pharmacokinetics, warnings, and
					recent FDA recall classes for any medication present in the corpus. Unknown medications
					are ingested on demand from public registries before retrieval runs.
				</p>
			</article>

			<article>
				<p class="eyebrow">§ 02 — How it grounds</p>
				<p>
					Hybrid retrieval over MedCPT-encoded labels and a BM25 index, reranked by a cross-encoder,
					with forced inline citations to the exact passage that supports each claim. A separate
					judge model audits faithfulness in the evaluation harness.
				</p>
			</article>

			<article>
				<p class="eyebrow">§ 03 — What it won&rsquo;t do</p>
				<p>
					Diagnose, dose, or replace a clinician. When the retrieved sources can&rsquo;t answer the
					question, the system declines rather than guessing. Confirm any clinical decision with a
					licensed pharmacist or physician.
				</p>
			</article>
		</section>
	</main>

	<footer class="colophon">
		<p class="footnote">
			Educational reference. Not medical advice. Verify with a licensed clinician.
		</p>
		{#if corpus}
			<p class="footnote stats" aria-live="polite">{corpusLine}</p>
		{/if}
	</footer>
</div>

<style>
	.page {
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr auto;
		padding: var(--space-5) var(--space-6);
		padding-top: max(var(--space-5), env(safe-area-inset-top));
		padding-left: max(var(--space-6), env(safe-area-inset-left));
		padding-right: max(var(--space-6), env(safe-area-inset-right));
		padding-bottom: max(var(--space-4), env(safe-area-inset-bottom));
		color: var(--ink);
		background: var(--paper);
	}

	/* ── Masthead ─────────────────────────────────────────────────────── */

	.masthead {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--space-3);
		max-width: var(--col-main-wide);
		width: 100%;
		margin: 0 auto;
		padding-bottom: var(--space-4);
		border-bottom: 1px solid var(--rule);
	}

	.brand {
		display: inline-flex;
		align-items: center;
		gap: var(--space-3);
		color: var(--ink);
		text-decoration: none;
	}

	.brand:hover {
		text-decoration: none;
	}

	.brand-mark {
		display: grid;
		place-items: center;
		width: 2.1rem;
		height: 2.1rem;
		color: var(--accent);
		border: 1px solid var(--accent);
		border-radius: var(--radius-2);
		padding: 0.2rem;
		font-feature-settings: normal;
	}

	.brand-mark :global(svg) {
		width: 100%;
		height: 100%;
	}

	.brand-wordmark {
		font-family: var(--font-prose);
		font-style: italic;
		font-weight: 500;
		font-size: 1.1rem;
		letter-spacing: -0.012em;
		color: var(--ink);
	}

	.icon-btn {
		display: inline-grid;
		place-items: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: var(--radius-2);
		color: var(--ink-muted);
		transition: color 0.16s ease, background 0.16s ease;
	}

	.icon-btn :global(svg) {
		width: 1.05rem;
		height: 1.05rem;
	}

	.icon-btn:hover {
		color: var(--ink);
		background: var(--paper-sunk);
	}

	@media (pointer: coarse) {
		.icon-btn {
			min-width: var(--tap);
			min-height: var(--tap);
			width: var(--tap);
			height: var(--tap);
		}
	}

	/* ── Document ─────────────────────────────────────────────────────── */

	.document {
		max-width: var(--col-main-wide);
		width: 100%;
		margin: 0 auto;
		padding: var(--space-7) 0 var(--space-6);
		display: flex;
		flex-direction: column;
		gap: var(--space-6);
	}

	/* ── Hero ─────────────────────────────────────────────────────────── */

	.hero {
		max-width: 38rem;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.section-no {
		margin: 0;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		letter-spacing: 0.08em;
		color: var(--accent);
	}

	.opening {
		margin: 0;
		font-family: var(--font-prose);
		font-weight: 400;
		font-size: var(--text-2xl);
		line-height: 1.14;
		letter-spacing: -0.02em;
		color: var(--ink);
		max-width: 18ch;
		font-variation-settings: 'opsz' 56;
	}

	.body {
		margin: 0;
		font-size: var(--text-base);
		line-height: 1.6;
		color: var(--ink-soft);
		max-width: var(--measure-narrow);
	}

	/* ── CTA ──────────────────────────────────────────────────────────── */

	.cta-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3) var(--space-5);
		margin-top: var(--space-3);
	}

	.cta {
		display: inline-flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.85rem 1.15rem;
		background: var(--ink);
		color: var(--paper);
		border: 1px solid var(--ink);
		border-radius: var(--radius-2);
		font-size: var(--text-base);
		font-weight: 500;
		text-decoration: none;
		letter-spacing: -0.005em;
		min-height: var(--tap);
		transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease, transform 0.18s ease;
	}

	.cta:hover {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--paper);
		text-decoration: none;
	}

	.cta:active {
		transform: translateY(1px);
	}

	.cta-cue {
		font-family: var(--font-mono);
		opacity: 0.85;
		transition: transform 0.2s cubic-bezier(0.22, 0.61, 0.36, 1);
	}

	.cta:hover .cta-cue {
		transform: translateX(3px);
	}

	.cta-meta {
		font-size: var(--text-xs);
		color: var(--ink-faint);
		letter-spacing: 0.02em;
	}

	/* ── Rule + manifest ──────────────────────────────────────────────── */

	.rule {
		border: 0;
		border-top: 1px solid var(--rule);
		margin: 0;
	}

	.manifest {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		column-gap: var(--space-6);
		row-gap: var(--space-5);
	}

	.manifest article {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.eyebrow {
		margin: 0;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.manifest p:not(.eyebrow) {
		margin: 0;
		font-size: var(--text-sm);
		line-height: 1.6;
		color: var(--ink-soft);
		max-width: 32ch;
	}

	/* ── Colophon ─────────────────────────────────────────────────────── */

	.colophon {
		max-width: var(--col-main-wide);
		width: 100%;
		margin: 0 auto;
		padding-top: var(--space-4);
		border-top: 1px solid var(--rule);
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--space-4);
		flex-wrap: wrap;
	}

	.footnote {
		margin: 0;
		font-size: var(--text-xs);
		color: var(--ink-faint);
		letter-spacing: 0.01em;
	}

	.footnote.stats {
		font-family: var(--font-mono);
		text-align: right;
	}

	/* ── Responsive ───────────────────────────────────────────────────── */

	@media (max-width: 920px) {
		.page {
			padding: var(--space-4);
			padding-top: max(var(--space-4), env(safe-area-inset-top));
		}

		.document {
			padding: var(--space-6) 0 var(--space-5);
			gap: var(--space-5);
		}

		.opening {
			font-size: var(--text-xl);
			max-width: none;
			font-variation-settings: 'opsz' 36;
		}

		.manifest {
			grid-template-columns: 1fr;
			row-gap: var(--space-4);
		}

		.manifest article {
			padding-top: var(--space-3);
			border-top: 1px solid var(--rule);
		}

		.manifest article:first-child {
			padding-top: 0;
			border-top: 0;
		}

		.manifest p:not(.eyebrow) {
			max-width: var(--measure-prose);
		}

		.colophon {
			flex-direction: column;
			align-items: flex-start;
			gap: var(--space-2);
		}

		.footnote.stats {
			text-align: left;
		}
	}

	@media (max-width: 640px) {
		.opening {
			font-size: 1.55rem;
			line-height: 1.18;
			font-variation-settings: 'opsz' 28;
		}

		.brand-wordmark {
			font-size: var(--text-base);
		}

		.cta-row {
			flex-direction: column;
			align-items: flex-start;
			gap: var(--space-3);
		}

		.cta {
			width: 100%;
			justify-content: center;
		}
	}

	@media (min-width: 1280px) {
		.document {
			padding-top: var(--space-8);
		}

		.opening {
			font-size: 2.6rem;
			font-variation-settings: 'opsz' 60;
		}
	}

	@media (min-width: 1600px) {
		.opening {
			font-size: 3rem;
			font-variation-settings: 'opsz' 72;
		}
	}
</style>
