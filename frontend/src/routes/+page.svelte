<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { browser } from '$app/environment';
	import DOMPurify from 'isomorphic-dompurify';

	import { applyTheme, resolveInitialTheme, type Theme } from '$lib/theme';

	const SESSIONS_STORAGE_KEY = 'medication-reference-chat-sessions-v1';
	const DEV_MODE_STORAGE_KEY = 'medication-reference-dev-mode';

	type Role = 'user' | 'assistant';
	type RecallSeverity = 'class1' | 'class2' | 'class3' | 'unknown';
	type RecallAlert = {
		id: string;
		drugName: string;
		severity: RecallSeverity;
		label: string;
		sourceUrl: string;
	};
	type ChatTurn = {
		id: string;
		role: Role;
		content: string;
		recallAlerts?: RecallAlert[];
		/** Ordered like CONTEXT [1]…[n]; maps bracket n → sources[n-1]. */
		citationSources?: Source[];
	};
	type StatusEntry = { id: string; text: string };

	type CorpusStats = {
		n_chunks: number;
		n_drugs: number;
		n_sources: number;
		embedding_model: string;
		collection: string;
		drugs?: string[];
	};

	type MetadataValue = string | number | boolean | null | undefined;
	type Source = {
		id: string;
		text: string;
		metadata: Record<string, MetadataValue>;
		score: number;
	};

	type PipelineResult = {
		question: string;
		redaction: {
			original: string;
			redacted: string;
			entities: { entity_type: string }[];
		};
		detected_drugs: { mention: string; canonical: string; rxcui: string; score: number }[];
		auto_ingest: {
			missing: { mention: string; canonical: string; rxcui: string; score: number }[];
			ingested: string[];
			added_chunks: number;
			error: string | null;
			skipped: boolean;
		};
		retrieved: Source[];
		reranked: Source[];
		generation: {
			answer: string;
			model: string;
			prompt_tokens: number | null;
			completion_tokens: number | null;
		} | null;
		timing: Record<string, number>;
		error: string | null;
		warnings?: Array<{
			original_token: string;
			presidio_entity: string;
			presidio_score: number;
			rxnorm_hit: boolean;
			rxcui: string;
			warning: string;
		}>;
		status_log?: string[];
	};

	type ChatSession = {
		id: string;
		title: string;
		createdAt: number;
		updatedAt: number;
		messages: ChatTurn[];
		statusLines: StatusEntry[];
		lastResult: PipelineResult | null;
		requestError: string | null;
	};

	const sampleQuestions = [
		'Can I take ibuprofen with lisinopril for my blood pressure?',
		'Is it safe to combine warfarin and aspirin?',
		"I'm John Smith (john@example.com). Does metformin interact with alcohol?",
		'What happens if I take sertraline and tramadol together?',
		'Can I take omeprazole while on clopidogrel?',
		'Does rifampin reduce the effectiveness of warfarin?'
	];

	let msgSeq = 0;
	let chatSeq = 0;
	let statusSeq = 0;

	function nextMsgId() {
		msgSeq += 1;
		return `msg-${msgSeq}`;
	}

	function nextChatId() {
		chatSeq += 1;
		return `chat-${chatSeq}`;
	}

	function nextStatusId() {
		statusSeq += 1;
		return `status-${statusSeq}`;
	}

	let genSeq = 0;

	function nextGenerationId() {
		genSeq += 1;
		return genSeq;
	}

	type Inflight = { controller: AbortController; generationId: number };
	/** Late SSE callbacks check the generationId here — entries dropped by cancelInflightForChat make handlers no-op. */
	const inflightByChatId = new Map<string, Inflight>();

	function cancelInflightForChat(chatId: string): boolean {
		const inflight = inflightByChatId.get(chatId);
		if (!inflight) return false;
		inflight.controller.abort();
		inflightByChatId.delete(chatId);
		return true;
	}

	function isActiveGeneration(chatId: string, generationId: number): boolean {
		return inflightByChatId.get(chatId)?.generationId === generationId;
	}

	function createChatSession(): ChatSession {
		const now = Date.now();
		return {
			id: nextChatId(),
			title: 'Empty conversation',
			createdAt: now,
			updatedAt: now,
			messages: [],
			statusLines: [],
			lastResult: null,
			requestError: null
		};
	}

	let initialChat = createChatSession();
	let sessions = $state<ChatSession[]>([initialChat]);
	let activeChatId = $state(initialChat.id);
	/** After true, session writes to localStorage are allowed (avoids clobbering before restore). */
	let sessionStorageReady = $state(false);
	/** Default closed so mobile CSS (drawer) does not flash open before hydration. Desktop is expanded in onMount. */
	let railCollapsed = $state(true);

	let isMobile = $state(false);
	/** Last mqMobile.matches — used to close the rail when switching from desktop → mobile. */
	let wasMobile = false;
	let controlsOpen = $state(false);

	/** Pipeline debug UI, corpus/model panels, and retrieval sliders — never shown to end users. */
	let devModeEnabled = $state(false);

	let showMobileBackdrop = $derived(isMobile && (!railCollapsed || (devModeEnabled && controlsOpen)));

	function dismissMobileOverlays() {
		railCollapsed = true;
		controlsOpen = false;
		const target = railTriggerEl;
		railTriggerEl = null;
		queueMicrotask(() => target?.focus());
	}

	function toggleDevMode() {
		if (!browser) return;
		try {
			if (sessionStorage.getItem(DEV_MODE_STORAGE_KEY) === '1') {
				sessionStorage.removeItem(DEV_MODE_STORAGE_KEY);
				devModeEnabled = false;
			} else {
				sessionStorage.setItem(DEV_MODE_STORAGE_KEY, '1');
				devModeEnabled = true;
			}
		} catch {
			devModeEnabled = !devModeEnabled;
		}
	}

	function toggleTheme() {
		theme = theme === 'light' ? 'dark' : 'light';
		applyTheme(theme);
	}

	function toggleRailCollapsed() {
		if (railCollapsed) {
			railCollapsed = false;
			if (isMobile) {
				controlsOpen = false;
			}
		} else {
			railCollapsed = true;
		}
	}

	function openMobileSessionRail(event?: MouseEvent) {
		const target = event?.currentTarget as HTMLButtonElement | undefined;
		if (target) railTriggerEl = target;
		railCollapsed = false;
		if (isMobile) controlsOpen = false;
		queueMicrotask(() => {
			if (!railEl) return;
			const items = focusableInside(railEl);
			items[0]?.focus();
		});
	}

	function onControlsToggle(e: ToggleEvent & { currentTarget: HTMLDetailsElement }) {
		if (!isMobile || !e.currentTarget.open) return;
		railCollapsed = true;
	}

	let corpus = $state<CorpusStats | null>(null);
	let apiConfig = $state<{
		pii_backend: string;
		has_openrouter_key: boolean;
	} | null>(null);

	let topKRetrieve = $state(20);
	let topKRerank = $state(5);
	let autoIngest = $state(true);
	let skipGeneration = $state(false);

	let input = $state('');
	let loading = $state(false);
	/** Narrow viewport: pipeline block starts collapsed so the message thread keeps height. */
	let compactPipelineUi = $state(false);
	/** false until matchMedia runs so mobile first paint is not an expanded 34vh block */
	let pipelineDetailsOpen = $state(false);
	let threadEl = $state<HTMLDivElement | undefined>(undefined);
	let lastSentUserId = $state<string | null>(null);
	let streamingMsgId = $state<string | null>(null);
	let streamingPreAnswer = $state(false);

	let theme = $state<Theme>('light');

	/** When opening the mobile rail, we trap focus inside it; this stores where to return to. */
	let railTriggerEl: HTMLButtonElement | null = null;
	let railEl = $state<HTMLElement | undefined>(undefined);
	let composerInputEl: HTMLTextAreaElement | undefined;

	function focusableInside(root: HTMLElement): HTMLElement[] {
		const sel = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';
		return Array.from(root.querySelectorAll<HTMLElement>(sel)).filter(
			(el) => !el.hasAttribute('inert') && el.offsetParent !== null
		);
	}

	function trapRailKeydown(event: KeyboardEvent) {
		if (event.key !== 'Tab' || !isMobile || railCollapsed || !railEl) return;
		const items = focusableInside(railEl);
		if (items.length === 0) return;
		const first = items[0];
		const last = items[items.length - 1];
		const active = document.activeElement as HTMLElement | null;
		if (event.shiftKey && active === first) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && active === last) {
			event.preventDefault();
			first.focus();
		}
	}

	function focusComposer() {
		composerInputEl?.focus();
	}

	let activeChat = $derived(sessions.find((session) => session.id === activeChatId) ?? sessions[0]);
	let currentMessages = $derived(activeChat?.messages ?? []);
	let hasAssistantReply = $derived(currentMessages.some((m) => m.role === 'assistant'));
	let currentStatus = $derived(activeChat?.statusLines ?? []);
	let currentResult = $derived(activeChat?.lastResult ?? null);
	let currentError = $derived(activeChat?.requestError ?? null);
	let latestStatus = $derived(currentStatus.at(-1)?.text ?? '');
	let hasMessages = $derived(currentMessages.length > 0);

	async function scrollThreadToEnd(behavior: ScrollBehavior = 'smooth') {
		await tick();
		threadEl?.scrollTo({ top: threadEl.scrollHeight, behavior });
	}

	/** True when the thread is scrolled to the bottom (within 32px slack). Used so streaming
	 * doesn't yank the viewport away from a user who scrolled up to read earlier content. */
	function isThreadPinnedToBottom(): boolean {
		if (!threadEl) return true;
		return threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight < 32;
	}

	function updateChat(chatId: string, updater: (session: ChatSession) => ChatSession) {
		sessions = sessions.map((session) => (session.id === chatId ? updater(session) : session));
	}

	function setChatMessages(chatId: string, messages: ChatTurn[]) {
		updateChat(chatId, (session) => ({ ...session, messages, updatedAt: Date.now() }));
	}

	function appendStatus(chatId: string, text: string) {
		updateChat(chatId, (session) => ({
			...session,
			statusLines: [...session.statusLines, { id: nextStatusId(), text }],
			updatedAt: Date.now()
		}));
	}

	function statusEntries(lines: string[]): StatusEntry[] {
		return lines.map((text) => ({ id: nextStatusId(), text }));
	}

	function titleFromQuestion(question: string) {
		const clean = question.replace(/\s+/g, ' ').trim();
		return clean.length > 42 ? `${clean.slice(0, 39)}...` : clean || 'New chat';
	}

	function firstUserContent(session: ChatSession): string | undefined {
		const turn = session.messages.find((m) => m.role === 'user');
		const t = turn?.content?.replace(/\s+/g, ' ').trim();
		return t || undefined;
	}

	/** Sidebar primary line: always follows the first user question when present. */
	function chatListTitle(session: ChatSession): string {
		const first = firstUserContent(session);
		if (first) return titleFromQuestion(first);
		return 'Empty conversation';
	}

	/** Sidebar secondary line: status / last activity snippet. */
	function chatListPreview(session: ChatSession): string {
		const msgs = session.messages;
		if (msgs.length === 0) return 'Ask your first question below';
		const last = msgs.at(-1)!;
		if (last.role === 'user' && msgs.length === 1) return 'Awaiting response…';
		const raw = last.content.replace(/\s+/g, ' ').trim() || '…';
		return raw.length > 52 ? `${raw.slice(0, 49)}…` : raw;
	}

	function esc(value: string): string {
		return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
	}

	function escAttr(value: string): string {
		return value
			.replace(/&/g, '&amp;')
			.replace(/"/g, '&quot;')
			.replace(/'/g, '&#39;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;');
	}

	function messageClass(message: ChatTurn) {
		return [
			'turn',
			message.role,
			message.id === lastSentUserId && 'sent-flash',
			message.id === streamingMsgId && 'streaming',
			message.id === streamingMsgId && streamingPreAnswer && 'pre-answer'
		]
			.filter(Boolean)
			.join(' ');
	}

	function formatTime(value: number) {
		return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(value);
	}

	function formatScore(value: number | null | undefined) {
		if (typeof value !== 'number' || Number.isNaN(value)) return 'n/a';
		return value.toFixed(3);
	}

	function formatMs(value: number) {
		return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value.toFixed(0)}ms`;
	}

	function metadataValue(value: MetadataValue) {
		if (value === null || value === undefined) return '';
		return String(value);
	}

	/** First HTTP(S) URL from chunk metadata for opening as citation target. */
	function citationHref(source: Source | undefined): string | null {
		if (!source?.metadata) return null;
		const m = source.metadata;
		const keys = ['source_url', 'url', 'link', 'page_url'] as const;
		for (const k of keys) {
			const raw = m[k];
			const s = metadataValue(raw);
			if (s && /^https?:\/\//i.test(s.trim())) return s.trim();
		}
		return null;
	}

	function metadataLabel(source: Source) {
		const metadata = source.metadata ?? {};
		const label =
			metadata.drug ??
			metadata.drug_name ??
			metadata.title ??
			metadata.source ??
			metadata.source_name ??
			metadata.url ??
			metadata.file ??
			source.id;
		return String(label);
	}

	function citeMarkerHtml(n: number, citationSources?: Source[] | null): string {
		const src = citationSources?.[n - 1];
		const href = citationHref(src);
		if (href) {
			const label = src ? metadataLabel(src) : '';
			const title = escAttr(label ? `Source [${n}]: ${label}` : `Source [${n}]`);
			return `<a class="cite cite-link" href="${escAttr(href)}" target="_blank" rel="noopener noreferrer" title="${title}">[${n}]</a>`;
		}
		return `<span class="cite" title="${escAttr(`Passage ${n} (no URL in retrieved metadata)`)}">[${n}]</span>`;
	}

	function inlineMarkdown(text: string, citationSources?: Source[] | null): string {
		return esc(text)
			.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
			.replace(/_(.+?)_/g, '<em>$1</em>')
			.replace(/\[(\d+)\]/g, (_, d) => citeMarkerHtml(parseInt(d, 10), citationSources))
			.replace(/\n/g, '<br>');
	}

	function renderMarkdown(text: string, citationSources?: Source[] | null): string {
		const paragraphs = text.split(/\n\n+/).filter((paragraph) => paragraph.trim());
		if (!paragraphs.length) {
			const t = text.trim();
			return t ? `<p>${inlineMarkdown(t, citationSources)}</p>` : '';
		}

		return paragraphs
			.map((paragraph) => {
				const lines = paragraph.trim().split('\n');
				const firstLine = lines[0].trim();
				const headerMatch = firstLine.match(/^\*\*(.+?)\*\*$/);
				if (headerMatch) {
					const body = lines.slice(1).join('\n').trim();
					return `<section class="md-section"><h4>${esc(headerMatch[1])}</h4>${body ? `<p>${inlineMarkdown(body, citationSources)}</p>` : ''}</section>`;
				}
				return `<p>${inlineMarkdown(paragraph.trim(), citationSources)}</p>`;
			})
			.join('');
	}

	function safeMarkdownHtml(text: string, citationSources?: Source[] | null): string {
		const raw = renderMarkdown(text, citationSources);
		return DOMPurify.sanitize(raw, {
			ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'span', 'section', 'h4', 'a'],
			ALLOWED_ATTR: ['class', 'href', 'target', 'rel', 'title']
		});
	}

	function entityTypes(result: PipelineResult) {
		return [...new Set(result.redaction.entities.map((entity) => entity.entity_type))].join(', ');
	}

	function recallSeverityFromSection(section: string): RecallSeverity {
		if (/class\s*iii\b/i.test(section)) return 'class3';
		if (/class\s*ii\b/i.test(section)) return 'class2';
		if (/class\s*i\b/i.test(section)) return 'class1';
		return 'unknown';
	}

	function recallLabelForSeverity(sev: RecallSeverity): string {
		if (sev === 'class1') return 'Class I recall';
		if (sev === 'class2') return 'Class II recall';
		if (sev === 'class3') return 'Class III recall';
		return 'FDA recall';
	}

	/** Union retrieved + reranked so recalls dropped by reranking still surface in the UI. */
	function extractRecallAlerts(result: PipelineResult): RecallAlert[] {
		const merged = [...(result.retrieved ?? []), ...(result.reranked ?? [])];
		const seen = new Set<string>();
		const list: RecallAlert[] = [];
		for (const src of merged) {
			const meta = src.metadata ?? {};
			if (String(meta.source ?? '') !== 'openfda_recall') continue;
			if (seen.has(src.id)) continue;
			const section = String(meta.section ?? '');
			const sev = recallSeverityFromSection(section);
			const drugName = String(meta.drug_name ?? 'unknown');
			const url = String(meta.source_url ?? '');
			if (!url.startsWith('http')) continue;
			const label = recallLabelForSeverity(sev);
			seen.add(src.id);
			list.push({
				id: src.id,
				drugName,
				severity: sev,
				label,
				sourceUrl: url
			});
		}
		list.sort((a, b) => {
			const rank = (s: RecallSeverity) =>
				s === 'class1' ? 0 : s === 'class2' ? 1 : s === 'class3' ? 2 : 3;
			const d = rank(a.severity) - rank(b.severity);
			if (d !== 0) return d;
			return a.drugName.localeCompare(b.drugName);
		});
		return list;
	}

	async function loadMeta() {
		try {
			const [stats, config] = await Promise.all([
				fetch('/api/corpus/stats').then((response) => response.json()),
				fetch('/api/config').then((response) => response.json())
			]);
			corpus = stats;
			apiConfig = config;
			if (!config.has_openrouter_key) skipGeneration = true;
		} catch {
			corpus = null;
		}
	}

	$effect(() => {
		void loadMeta();
	});

	$effect(() => {
		if (!devModeEnabled) controlsOpen = false;
	});

	onMount(() => {
		if (!browser) return;
		if (!window.matchMedia('(max-width: 920px)').matches) {
			railCollapsed = false;
		}
		theme = resolveInitialTheme();
		applyTheme(theme);

		try {
			const params = new URLSearchParams(window.location.search);
			if (params.get('dev') === 'true') {
				sessionStorage.setItem(DEV_MODE_STORAGE_KEY, '1');
				params.delete('dev');
				const qs = params.toString();
				const next = qs ? `${window.location.pathname}?${qs}${window.location.hash}` : `${window.location.pathname}${window.location.hash}`;
				history.replaceState({}, '', next);
			}
			devModeEnabled = sessionStorage.getItem(DEV_MODE_STORAGE_KEY) === '1';
		} catch {
			devModeEnabled = false;
		}
		try {
			const raw = localStorage.getItem(SESSIONS_STORAGE_KEY);
			if (raw) {
				const data = JSON.parse(raw) as {
					sessions?: ChatSession[];
					activeChatId?: string;
					msgSeq?: number;
					chatSeq?: number;
					statusSeq?: number;
				};
				if (Array.isArray(data.sessions) && data.sessions.length > 0) {
					sessions = data.sessions;
					if (data.activeChatId && data.sessions.some((s) => s.id === data.activeChatId)) {
						activeChatId = data.activeChatId;
					} else {
						activeChatId = data.sessions[0].id;
					}
					if (typeof data.msgSeq === 'number') msgSeq = data.msgSeq;
					if (typeof data.chatSeq === 'number') chatSeq = data.chatSeq;
					if (typeof data.statusSeq === 'number') statusSeq = data.statusSeq;
				}
			}
		} catch {
			// ignore corrupt storage
		} finally {
			sessionStorageReady = true;
		}
	});

	onDestroy(() => {
		inflightByChatId.forEach(({ controller }) => controller.abort());
		inflightByChatId.clear();
	});

	$effect(() => {
		if (!browser || !sessionStorageReady) return;
		void sessions;
		void activeChatId;
		void msgSeq;
		void chatSeq;
		void statusSeq;
		try {
			localStorage.setItem(
				SESSIONS_STORAGE_KEY,
				JSON.stringify({ sessions, activeChatId, msgSeq, chatSeq, statusSeq })
			);
		} catch {
			// quota / private mode
		}
	});

	$effect(() => {
		const mqMobile = window.matchMedia('(max-width: 920px)');
		const mqCompact = window.matchMedia('(max-width: 640px)');
		const sync = () => {
			const nextMobile = mqMobile.matches;
			if (nextMobile && !wasMobile) {
				railCollapsed = true;
			} else if (!nextMobile && wasMobile) {
				railCollapsed = false;
			}
			wasMobile = nextMobile;
			isMobile = nextMobile;
			compactPipelineUi = mqCompact.matches;
			pipelineDetailsOpen = !mqCompact.matches;
		};
		sync();
		mqMobile.addEventListener('change', sync);
		mqCompact.addEventListener('change', sync);
		return () => {
			mqMobile.removeEventListener('change', sync);
			mqCompact.removeEventListener('change', sync);
		};
	});

	$effect(() => {
		if (!browser) return;
		/* The mobile rail uses `overscroll-behavior: contain` (see CSS), which is sufficient
		 * to prevent scroll chaining without the iOS scroll-jump caused by `body { overflow: hidden }`. */
	});

	function startNewChat() {
		if (cancelInflightForChat(activeChatId)) {
			loading = false;
			streamingMsgId = null;
			streamingPreAnswer = false;
		}
		const session = createChatSession();
		sessions = [session, ...sessions];
		activeChatId = session.id;
		input = '';
		if (isMobile) {
			railCollapsed = true;
		}
		void scrollThreadToEnd('auto');
	}

	function selectChat(chatId: string) {
		if (chatId !== activeChatId && cancelInflightForChat(activeChatId)) {
			loading = false;
			streamingMsgId = null;
			streamingPreAnswer = false;
		}
		activeChatId = chatId;
		if (isMobile) {
			railCollapsed = true;
		}
		void scrollThreadToEnd('auto');
	}

	function deleteChat(chatId: string, event?: MouseEvent) {
		event?.stopPropagation();
		cancelInflightForChat(chatId);
		if (activeChatId === chatId) {
			loading = false;
			streamingMsgId = null;
			streamingPreAnswer = false;
		}
		const filtered = sessions.filter((s) => s.id !== chatId);
		if (filtered.length === 0) {
			const session = createChatSession();
			sessions = [session];
			activeChatId = session.id;
			input = '';
			if (isMobile) railCollapsed = true;
			void scrollThreadToEnd('auto');
			return;
		}
		sessions = filtered;
		if (activeChatId === chatId) {
			activeChatId = filtered[0].id;
			input = '';
			void scrollThreadToEnd('auto');
		}
	}

	function clearActiveChat() {
		if (cancelInflightForChat(activeChatId)) {
			loading = false;
			streamingMsgId = null;
			streamingPreAnswer = false;
		}
		updateChat(activeChatId, (session) => ({
			...session,
			title: 'Empty conversation',
			messages: [],
			statusLines: [],
			lastResult: null,
			requestError: null,
			updatedAt: Date.now()
		}));
		input = '';
	}

	function buildHistory(messages: ChatTurn[]): ChatTurn[] {
		const history: ChatTurn[] = [];
		for (let index = 0; index + 1 < messages.length; index += 2) {
			const user = messages[index];
			const assistant = messages[index + 1];
			if (user?.role === 'user' && assistant?.role === 'assistant') {
				history.push(user, assistant);
			}
		}
		return history;
	}

	async function send(question: string) {
		const q = question.trim();
		if (!q || loading || !activeChat) return;

		const chatId = activeChat.id;
		// Supersede any prior in-flight request for this chat before starting a new one.
		cancelInflightForChat(chatId);
		const generationId = nextGenerationId();
		const controller = new AbortController();
		inflightByChatId.set(chatId, { controller, generationId });

		const previousMessages = [...activeChat.messages];
		const userMessage: ChatTurn = { id: nextMsgId(), role: 'user', content: q };
		const nextTitle = previousMessages.length ? activeChat.title : titleFromQuestion(q);

		updateChat(chatId, (session) => ({
			...session,
			title: nextTitle,
			messages: [...previousMessages, userMessage],
			statusLines: [],
			lastResult: null,
			requestError: null,
			updatedAt: Date.now()
		}));

		lastSentUserId = userMessage.id;
		setTimeout(() => {
			lastSentUserId = null;
		}, 700);

		input = '';
		loading = true;
		streamingPreAnswer = false;
		void scrollThreadToEnd();

		const history = buildHistory(previousMessages);
		let assistantId: string | null = null;
		let streamedContent = '';
		let hadPreAnswer = false;

		/** rAF coalesce: many tokens may arrive within one frame; flush once per frame. */
		let pendingFlush = false;
		let pendingScroll = false;
		function scheduleFlush() {
			if (pendingFlush) return;
			pendingFlush = true;
			requestAnimationFrame(() => {
				pendingFlush = false;
				if (!isActiveGeneration(chatId, generationId)) return;
				updateAssistantContent(streamedContent);
				if (pendingScroll && activeChatId === chatId && isThreadPinnedToBottom()) {
					threadEl?.scrollTo({ top: threadEl.scrollHeight, behavior: 'auto' });
				}
				pendingScroll = false;
			});
		}

		function ensureAssistantBubble() {
			if (assistantId !== null) return;
			assistantId = nextMsgId();
			streamingMsgId = assistantId;
			setChatMessages(chatId, [
				...previousMessages,
				userMessage,
				{ id: assistantId, role: 'assistant', content: '' }
			]);
		}

		function updateAssistantContent(content: string) {
			if (assistantId === null) return;
			updateChat(chatId, (session) => ({
				...session,
				messages: session.messages.map((message) =>
					message.id === assistantId ? { ...message, content } : message
				),
				updatedAt: Date.now()
			}));
		}

		function removeAssistantBubble() {
			if (assistantId === null) return;
			updateChat(chatId, (session) => ({
				...session,
				messages: session.messages.filter((message) => message.id !== assistantId),
				updatedAt: Date.now()
			}));
			assistantId = null;
		}

		function handleSSEEvent(event: string, rawData: string) {
			if (!isActiveGeneration(chatId, generationId)) return;
			try {
				const data = JSON.parse(rawData);

				if (event === 'status') {
					appendStatus(chatId, typeof data === 'string' ? data : String(data));
				} else if (event === 'pre_answer') {
					ensureAssistantBubble();
					streamedContent = typeof data === 'string' ? data : String(data);
					hadPreAnswer = true;
					streamingPreAnswer = true;
					pendingScroll = true;
					scheduleFlush();
				} else if (event === 'token') {
					ensureAssistantBubble();
					if (hadPreAnswer) {
						streamedContent = '';
						hadPreAnswer = false;
						streamingPreAnswer = false;
					}
					streamedContent += typeof data === 'string' ? data : String(data);
					pendingScroll = true;
					scheduleFlush();
				} else if (event === 'result') {
					const result = data as PipelineResult;
					// Ensure assistant row exists so recalls attach even when there were no streamed tokens.
					ensureAssistantBubble();
					const recalls = extractRecallAlerts(result);
					updateChat(chatId, (session) => {
						let messages = session.messages;
						if (assistantId !== null) {
							messages = session.messages.map((m) =>
								m.id === assistantId
									? {
											...m,
											recallAlerts: recalls.length ? recalls : undefined,
											citationSources:
												result.reranked?.length ? [...result.reranked] : undefined
										}
									: m
							);
						}
						return {
							...session,
							messages,
							lastResult: result,
							statusLines: session.statusLines.length
								? session.statusLines
								: statusEntries(result.status_log ?? []),
							requestError: result.error,
							updatedAt: Date.now()
						};
					});

					if (!streamedContent) {
						ensureAssistantBubble();
						const answer =
							result.generation?.answer ??
							(result.error ? `_(generation failed: ${result.error})_` : '_(LLM call skipped - retrieval only)_');
						streamedContent = answer;
						updateAssistantContent(answer);
					}

					void loadMeta();
					if (activeChatId === chatId) void scrollThreadToEnd('auto');
				} else if (event === 'error') {
					const message = typeof data === 'string' ? data : JSON.stringify(data);
					updateChat(chatId, (session) => ({ ...session, requestError: message, updatedAt: Date.now() }));
					if (!streamedContent) removeAssistantBubble();
				}
			} catch {
				// Malformed stream frames are ignored so one bad event does not break the response.
			}
		}

		try {
			const response = await fetch('/api/chat/stream', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					question: q,
					history,
					top_k_retrieve: topKRetrieve,
					top_k_rerank: topKRerank,
					auto_ingest: autoIngest,
					skip_generation: skipGeneration
				}),
				signal: controller.signal
			});

			if (!response.ok || !response.body) {
				const errData = await response.json().catch(() => ({ detail: response.statusText }));
				throw new Error(typeof errData.detail === 'string' ? errData.detail : response.statusText);
			}

			const reader = response.body.getReader();
			const decoder = new TextDecoder();
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });

				const parts = buffer.split('\n\n');
				buffer = parts.pop() ?? '';

				for (const part of parts) {
					if (!part.trim()) continue;

					let event = '';
					const dataLines: string[] = [];
					for (const line of part.split('\n')) {
						if (line.startsWith('event: ')) event = line.slice(7).trim();
						else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
					}

					if (event && dataLines.length) handleSSEEvent(event, dataLines.join('\n'));
				}
			}
		} catch (error) {
			// Aborts (chat switch, clear, new send, unmount) are user-initiated — surface no error.
			if (controller.signal.aborted || !isActiveGeneration(chatId, generationId)) return;
			const message = error instanceof Error ? error.message : 'Request failed';
			updateChat(chatId, (session) => ({ ...session, requestError: message, updatedAt: Date.now() }));
			if (!streamedContent) removeAssistantBubble();
		} finally {
			if (inflightByChatId.get(chatId)?.generationId === generationId) {
				inflightByChatId.delete(chatId);
			}
			if (chatId === activeChatId) {
				loading = false;
				streamingMsgId = null;
				streamingPreAnswer = false;
			}
		}
	}

	function onSubmit(event: SubmitEvent) {
		event.preventDefault();
		void send(input);
	}

	function handleComposerKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			void send(input);
		}
	}
</script>

<svelte:head>
	<title>Medication Reference</title>
	<meta
		name="description"
		content="Ask about drug interactions and medication safety. Personal details are protected before lookup; answers cite FDA and NIH sources."
	/>
	<meta name="theme-color" content={theme === 'dark' ? '#1f1d24' : '#f8f6f0'} />
</svelte:head>

<svelte:window
	onkeydown={(e) => {
		if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'd') {
			e.preventDefault();
			toggleDevMode();
			return;
		}
		if (e.key === 'Escape' && showMobileBackdrop) {
			e.preventDefault();
			dismissMobileOverlays();
		}
	}}
/>

<a class="skip-link" href="#question-input" onclick={(e) => { e.preventDefault(); focusComposer(); }}>
	Skip to question input
</a>

<div class={['app-shell', railCollapsed && 'rail-minimized'].filter(Boolean).join(' ')}>
	{#snippet pharmaMark()}
		<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
			<text
				x="12"
				y="16.5"
				text-anchor="middle"
				fill="currentColor"
				font-size="14"
				font-family="Georgia, 'Times New Roman', 'Noto Serif', serif"
				font-weight="600"
			>℞</text>
		</svg>
	{/snippet}
	{#snippet railToggleIcon()}
		<svg class="rail-toggle-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">
			<path
				class="rail-toggle-path"
				d="M9 5l6 7-6 7"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
		</svg>
	{/snippet}
	{#if showMobileBackdrop}
		<button
			type="button"
			class="mobile-overlay-backdrop"
			aria-label="Close panel"
			tabindex="-1"
			onclick={dismissMobileOverlays}
		></button>
	{/if}
	<aside
		id="session-rail-panel"
		bind:this={railEl}
		class="session-rail"
		aria-label="Current session chats"
		role={isMobile ? 'dialog' : undefined}
		aria-modal={isMobile && !railCollapsed ? 'true' : undefined}
		onkeydown={trapRailKeydown}
	>
		<div class="rail-header">
			<a class="brand" href="/" aria-label="Medication Reference home">
				<span class="brand-mark" aria-hidden="true">{@render pharmaMark()}</span>
				{#if !railCollapsed}
					<span class="brand-wordmark">Medication<br />Reference</span>
				{/if}
			</a>
			<button
				type="button"
				class="rail-toggle"
				onclick={toggleRailCollapsed}
				aria-label={railCollapsed ? 'Expand chat history' : 'Collapse chat history'}
				aria-expanded={!railCollapsed}
			>
				{@render railToggleIcon()}
			</button>
		</div>

		<button type="button" class="rail-new" onclick={startNewChat} aria-label="Start a new chat">
			<span class="rail-new-glyph" aria-hidden="true">+</span>
			{#if !railCollapsed}<span>New question</span>{/if}
		</button>

		{#if !railCollapsed}
			<nav class="chat-list" aria-label="Chats in this session">
				<p class="chat-list-heading">Recent</p>
				{#each sessions as session, i (session.id)}
					<div
						class={['chat-row-wrap', session.id === activeChatId && 'active'].filter(Boolean).join(' ')}
					>
						<button
							type="button"
							class="chat-row"
							onclick={() => selectChat(session.id)}
							aria-current={session.id === activeChatId ? 'page' : undefined}
						>
							<span class="chat-row-num">{String(i + 1).padStart(2, '0')}</span>
							<span class="chat-row-body">
								<span class="chat-row-title">{chatListTitle(session)}</span>
								<span class="chat-row-preview">{chatListPreview(session)}</span>
							</span>
							<span class="chat-row-time">{formatTime(session.updatedAt)}</span>
						</button>
						<button
							type="button"
							class="chat-row-delete"
							aria-label={`Delete “${chatListTitle(session)}”`}
							onclick={(e: MouseEvent) => deleteChat(session.id, e)}
						>
							<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
								<path
									d="M4 7h16M10 11v6M14 11v6M6 7l1 12h10l1-12M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"
									stroke-linecap="round"
									stroke-linejoin="round"
								/>
							</svg>
						</button>
					</div>
				{/each}
			</nav>
		{/if}
	</aside>

	<main class="chat-main">
		<div class="main-max">
		<header class="topbar">
			<div class="topbar-left">
				{#if isMobile}
					<button
						type="button"
						class="icon-btn mobile-menu-btn"
						onclick={(e: MouseEvent) => openMobileSessionRail(e)}
						aria-label="Open chat history"
						aria-controls="session-rail-panel"
						aria-expanded={!railCollapsed}
					>
						<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
							<path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
						</svg>
					</button>
				{/if}
				<p class="topbar-meta">
					<span>Drug interactions, indications &amp; recalls</span>
					<span class="topbar-sep" aria-hidden="true">·</span>
					<span>FDA &amp; NIH sources</span>
				</p>
			</div>

			<div class="topbar-actions">
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
							<path d="M12 3v2M12 19v2M5 12H3M21 12h-2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" stroke-linecap="round" />
						</svg>
					{/if}
				</button>
				<button type="button" class="text-btn" onclick={clearActiveChat} disabled={!hasMessages && !currentResult}>
					Clear
				</button>
			</div>
		</header>

		{#if devModeEnabled && hasAssistantReply}
			<section class="meta-row" aria-label="Corpus and model metadata">
				<details class="meta-card">
					<summary>Corpus</summary>
					{#if corpus}
						<div class="metric-grid">
							<div><strong>{corpus.n_chunks.toLocaleString()}</strong><span>chunks</span></div>
							<div><strong>{corpus.n_drugs.toLocaleString()}</strong><span>drugs</span></div>
							<div><strong>{corpus.n_sources.toLocaleString()}</strong><span>sources</span></div>
						</div>
						<p class="mono muted">{corpus.collection} / {corpus.embedding_model}</p>
					{:else}
						<p class="muted">Corpus stats unavailable. Check that the API is running.</p>
					{/if}
				</details>

				<details class="meta-card">
					<summary>Model &amp; privacy</summary>
					{#if apiConfig}
						<p><span class="muted">PII backend</span> <strong>{apiConfig.pii_backend}</strong></p>
						<p class={apiConfig.has_openrouter_key ? 'ok-text' : 'warn-text'}>
							{apiConfig.has_openrouter_key ? 'OpenRouter key available' : 'No OpenRouter key; retrieval-only mode enabled'}
						</p>
					{:else}
						<p class="muted">Config unavailable.</p>
					{/if}
				</details>
			</section>
		{/if}

		{#if currentError}
			<div class="banner error" role="alert">{currentError}</div>
		{/if}

		<div class="thread" bind:this={threadEl}>
			{#if !hasMessages}
				<section class="opening">
					<p class="opening-eyebrow">01 — Reference desk</p>
					<h1 class="opening-heading">
						Ask about a medication, a combination, or a recall.
					</h1>
					<p class="opening-body">
						Answers are drawn from retrieved FDA DailyMed labels, NIH MedlinePlus
						monographs, and OpenFDA recall records, with inline citations to the
						underlying sources. Personal details are redacted before retrieval.
						This is an educational reference, not medical advice.
					</p>

					<ol class="sample-list" aria-label="Example questions">
						{#each sampleQuestions as sample, i (sample)}
							<li>
								<button type="button" class="sample-row" disabled={loading} onclick={() => send(sample)}>
									<span class="sample-num">{String(i + 1).padStart(2, '0')}</span>
									<span class="sample-text">{sample}</span>
									<span class="sample-cue" aria-hidden="true">→</span>
								</button>
							</li>
						{/each}
					</ol>
				</section>
			{:else}
				<ol class="transcript">
					{#each currentMessages as message (message.id)}
						<li class={messageClass(message)}>
							{#if message.role === 'user'}
								<p class="turn-label">Question</p>
								<p class="question-text">{message.content}</p>
							{:else}
								<p class="turn-label">Answer</p>
								{#if message.recallAlerts?.length}
									<aside
										class="recall-strip"
										role="group"
										aria-label="FDA drug recalls found in sources for this answer"
									>
										<p class="recall-strip-title">FDA recalls in cited sources</p>
										<ul class="recall-list">
											{#each message.recallAlerts as alert (alert.id)}
												<li class={['recall-row', `recall-sev-${alert.severity}`].join(' ')}>
													<span class="recall-row-mark" aria-hidden="true">⚑</span>
													<span class="recall-row-label">{alert.label}</span>
													<span class="recall-row-drug">{alert.drugName}</span>
													<a
														class="recall-row-link"
														href={alert.sourceUrl}
														target="_blank"
														rel="noopener noreferrer"
													>FDA notice ↗</a>
												</li>
											{/each}
										</ul>
									</aside>
								{/if}
								<div class="answer-body">{@html safeMarkdownHtml(message.content, message.citationSources)}</div>
							{/if}
						</li>
					{/each}

					{#if loading && !streamingMsgId}
						<li class="turn assistant pending" aria-busy="true">
							<p class="turn-label">Answer</p>
							<p class="loading-status" role="status" aria-live="polite">
								<span class="dot-pulse" aria-hidden="true"><span></span><span></span><span></span></span>
								<span>{latestStatus || 'Searching sources…'}</span>
							</p>
						</li>
					{/if}
				</ol>
			{/if}
		</div>

		{#if devModeEnabled && (currentStatus.length || currentResult)}
			<details class="details-stack" bind:open={pipelineDetailsOpen}>
				<summary class="details-stack-summary">Pipeline &amp; sources</summary>
				<div class="details-stack-body">
				{#if currentStatus.length}
					<details class="detail-panel" open={loading}>
						<summary>Pipeline status</summary>
						<ol class="status-list">
							{#each currentStatus as status (status.id)}
								<li>{status.text}</li>
							{/each}
						</ol>
					</details>
				{/if}

				{#if currentResult}
					<details class="detail-panel">
						<summary>PII redaction</summary>
						<div class="split">
							<div>
								<h3>Original</h3>
								<pre>{currentResult.redaction.original}</pre>
							</div>
							<div>
								<h3>Redacted</h3>
								<pre>{currentResult.redaction.redacted}</pre>
							</div>
						</div>
						{#if currentResult.redaction.entities.length}
							<p class="note warn">
								Detected {currentResult.redaction.entities.length} PII entities: {entityTypes(currentResult)}
							</p>
						{:else}
							<p class="note ok">No PII entities detected.</p>
						{/if}
					</details>

					{#if currentResult.warnings && currentResult.warnings.length > 0}
						<details class="detail-panel">
							<summary>Redaction audit</summary>
							<p class="muted">PII redaction layer flagged {currentResult.warnings.length} span(s) that may have masked drug names.</p>
							<ul>
								{#each currentResult.warnings as w}
									<li>
										<strong>{w.original_token}</strong> — tagged as <code>{w.presidio_entity}</code>
										(score {w.presidio_score.toFixed(2)}).
										{#if w.rxnorm_hit}
											RxNorm matched <code>rxcui:{w.rxcui}</code>.
										{:else}
											No RxNorm match; pattern resembles a brand name.
										{/if}
									</li>
								{/each}
							</ul>
						</details>
					{/if}

					<details class="detail-panel">
						<summary>Drug detection &amp; auto-ingest</summary>
						{#if currentResult.detected_drugs.length}
							<div class="pill-list">
								{#each currentResult.detected_drugs as drug (`${drug.mention}-${drug.rxcui}`)}
									<span class="data-pill">
										<strong>{drug.canonical}</strong>
										<small>{drug.mention} / RxCUI {drug.rxcui} / {formatScore(drug.score)}</small>
									</span>
								{/each}
							</div>
						{:else}
							<p class="muted">No drug names were detected.</p>
						{/if}

						<div class="ingest-box">
							<p><strong>{currentResult.auto_ingest.skipped ? 'Auto-ingest skipped' : 'Auto-ingest checked'}</strong></p>
							<p class="muted">
								{currentResult.auto_ingest.ingested.length
									? `Ingested ${currentResult.auto_ingest.ingested.join(', ')}`
									: 'No new drug documents were ingested.'}
							</p>
							{#if currentResult.auto_ingest.added_chunks}
								<p class="ok-text">Added {currentResult.auto_ingest.added_chunks} chunks.</p>
							{/if}
							{#if currentResult.auto_ingest.error}
								<p class="warn-text">{currentResult.auto_ingest.error}</p>
							{/if}
						</div>
					</details>

					<details class="detail-panel" open={!compactPipelineUi}>
						<summary>Citations &amp; reranked sources</summary>
						{#if currentResult.reranked.length}
							<div class="source-list">
								{#each currentResult.reranked as source (source.id)}
									<article class="source-card">
										<div class="source-head">
											<strong>{metadataLabel(source)}</strong>
											<span class="score">score {formatScore(source.score)}</span>
										</div>
										<p>{source.text}</p>
										<div class="metadata">
											{#each Object.entries(source.metadata ?? {}) as [key, value] (`${source.id}-${key}`)}
												{#if metadataValue(value)}
													<span>{key}: {metadataValue(value)}</span>
												{/if}
											{/each}
										</div>
									</article>
								{/each}
							</div>
						{:else}
							<p class="muted">No reranked sources returned.</p>
						{/if}
					</details>

					<details class="detail-panel">
						<summary>Generation &amp; timing</summary>
						{#if currentResult.generation}
							<div class="metric-grid">
								<div><strong>{currentResult.generation.model}</strong><span>model</span></div>
								<div><strong>{currentResult.generation.prompt_tokens ?? 'n/a'}</strong><span>prompt tokens</span></div>
								<div>
									<strong>{currentResult.generation.completion_tokens ?? 'n/a'}</strong><span>completion tokens</span>
								</div>
							</div>
						{:else}
							<p class="muted">Generation skipped or unavailable.</p>
						{/if}

						{#if Object.keys(currentResult.timing ?? {}).length}
							<div class="timing-grid">
								{#each Object.entries(currentResult.timing) as [name, value] (name)}
									<div><span>{name}</span><strong>{formatMs(value)}</strong></div>
								{/each}
							</div>
						{/if}
					</details>
				{/if}
				</div>
			</details>
		{/if}

		<div class={['composer', controlsOpen && devModeEnabled && 'composer-drawer-active'].filter(Boolean).join(' ')}>
			<form class="composer-form" onsubmit={onSubmit}>
				<div class="composer-main">
					<label class="sr-only" for="question-input">Ask a drug interaction question</label>
					<textarea
						id="question-input"
						bind:this={composerInputEl}
						bind:value={input}
						onkeydown={handleComposerKeydown}
						placeholder="Type a question — e.g. ibuprofen with lisinopril"
						rows="2"
						disabled={loading}
					></textarea>
					<button type="submit" class="send" disabled={loading || !input.trim()} aria-label="Send question">
						{#if loading}
							<span class="dot-pulse" aria-hidden="true"><span></span><span></span><span></span></span>
						{:else}
							<span class="send-label">Send</span>
							<span class="send-cue" aria-hidden="true">↩</span>
						{/if}
					</button>
				</div>
				<p class="composer-hint">
					Enter to send · Shift+Enter for newline · Educational reference, not medical advice
				</p>
			</form>

			{#if devModeEnabled}
				<div class="composer-footer">
					<details class="composer-drawer" bind:open={controlsOpen} ontoggle={onControlsToggle}>
						<summary>Dev controls</summary>
						<div class="control-grid">
							<label>
								<span>Retrieve top-k</span>
								<input type="range" min="5" max="50" step="5" bind:value={topKRetrieve} />
								<output>{topKRetrieve}</output>
							</label>
							<label>
								<span>Rerank top-k</span>
								<input type="range" min="1" max="10" step="1" bind:value={topKRerank} />
								<output>{topKRerank}</output>
							</label>
							<label class="check-row">
								<input type="checkbox" bind:checked={autoIngest} />
								<span>Auto-ingest unknown drugs</span>
							</label>
							<label class="check-row">
								<input
									type="checkbox"
									bind:checked={skipGeneration}
									disabled={apiConfig?.has_openrouter_key === false}
								/>
								<span>Skip LLM</span>
							</label>
						</div>
					</details>
				</div>
			{/if}
		</div>
		</div>
	</main>
</div>

<style>
	/* ── Layout shell ─────────────────────────────────────────────────── */

	.app-shell {
		min-height: 100dvh;
		height: 100dvh;
		max-height: 100dvh;
		overflow: hidden;
		display: grid;
		grid-template-columns: var(--rail-w) minmax(0, 1fr);
		/* minmax(0,1fr) lets the row shrink below content min-size so overflow:hidden does not clip the composer */
		grid-template-rows: minmax(0, 1fr);
		background: var(--paper);
		color: var(--ink);
	}

	.app-shell.rail-minimized {
		grid-template-columns: var(--rail-w-collapsed) minmax(0, 1fr);
	}

	/* ── Sidebar / rail ───────────────────────────────────────────────── */

	.session-rail {
		border-right: 1px solid var(--rule);
		background: var(--paper);
		padding: var(--space-5) var(--space-4) var(--space-4);
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
		min-width: 0;
		min-height: 0;
		align-self: stretch;
	}

	.rail-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--space-3);
		min-height: 2.5rem;
	}

	.brand {
		display: inline-flex;
		align-items: center;
		gap: var(--space-3);
		color: var(--ink);
		text-decoration: none;
		min-width: 0;
	}

	.brand:hover {
		text-decoration: none;
	}

	.brand-mark {
		display: grid;
		place-items: center;
		width: 2.1rem;
		height: 2.1rem;
		flex: 0 0 auto;
		color: var(--accent);
		border: 1px solid var(--accent);
		border-radius: var(--radius-2);
		padding: 0.2rem;
		font-feature-settings: normal;
	}

	:global(.rail-minimized) .brand-mark {
		width: 1.7rem;
		height: 1.7rem;
		padding: 0.15rem;
	}

	.brand-mark :global(svg) {
		width: 100%;
		height: 100%;
		display: block;
	}

	.brand-wordmark {
		font-family: var(--font-prose);
		font-style: italic;
		font-weight: 500;
		font-size: 1.05rem;
		line-height: 1.05;
		letter-spacing: -0.01em;
		color: var(--ink);
	}

	.rail-toggle {
		display: inline-grid;
		place-items: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: var(--radius-2);
		color: var(--ink-muted);
		transition: color 0.16s ease, background 0.16s ease;
	}

	.rail-toggle:hover {
		color: var(--ink);
		background: var(--paper-sunk);
	}

	.rail-toggle :global(.rail-toggle-svg) {
		width: 0.95rem;
		height: 0.95rem;
	}

	.rail-toggle :global(.rail-toggle-path) {
		transform-origin: 12px 12px;
		transform: scaleX(-1);
	}

	:global(.rail-minimized) .rail-toggle :global(.rail-toggle-path) {
		transform: none;
	}

	.rail-new {
		display: inline-flex;
		align-items: center;
		gap: var(--space-3);
		padding: 0.55rem 0.7rem;
		font-size: var(--text-sm);
		font-weight: 500;
		color: var(--ink);
		border: 1px solid var(--rule);
		border-radius: var(--radius-2);
		background: var(--paper);
		transition: border-color 0.16s ease, color 0.16s ease;
	}

	.rail-new-glyph {
		display: inline-grid;
		place-items: center;
		width: 1.05rem;
		height: 1.05rem;
		font-size: 1.05rem;
		font-weight: 400;
		color: var(--ink-muted);
		line-height: 1;
	}

	.rail-new:hover .rail-new-glyph {
		color: var(--ink);
	}

	.rail-new:hover {
		border-color: var(--ink);
	}

	:global(.rail-minimized) .rail-new {
		justify-content: center;
		padding: 0.55rem;
	}

	.chat-list {
		display: flex;
		flex-direction: column;
		gap: 0;
		overflow: auto;
		min-height: 0;
		margin: var(--space-2) calc(var(--space-4) * -1) 0;
		padding: 0 var(--space-4);
	}

	.chat-list-heading {
		margin: 0 0 var(--space-2);
		font-size: var(--text-xs);
		letter-spacing: 0.18em;
		text-transform: uppercase;
		color: var(--ink-faint);
		font-weight: 500;
	}

	.chat-row-wrap {
		display: flex;
		align-items: stretch;
		gap: 0;
		border-top: 1px solid var(--rule);
	}

	.chat-row-wrap:first-of-type {
		border-top: 0;
	}

	.chat-row-wrap.active .chat-row {
		color: var(--ink);
	}

	.chat-row-wrap.active .chat-row-num {
		color: var(--accent);
	}

	.chat-row-wrap:hover .chat-row {
		color: var(--ink);
	}

	.chat-row-wrap:hover .chat-row-delete {
		color: var(--ink-muted);
	}

	.chat-row {
		display: grid;
		grid-template-columns: 1.5rem minmax(0, 1fr) auto;
		gap: var(--space-3);
		align-items: baseline;
		text-align: left;
		flex: 1;
		min-width: 0;
		padding: var(--space-3) 0;
		border: 0;
		background: transparent;
		color: var(--ink-soft);
		transition: color 0.16s ease;
	}

	.chat-row-delete {
		flex-shrink: 0;
		display: inline-grid;
		place-items: center;
		align-self: center;
		width: 2.25rem;
		height: 2.25rem;
		margin-right: calc(var(--space-2) * -0.5);
		padding: 0;
		border-radius: var(--radius-2);
		color: var(--ink-faint);
		transition: color 0.16s ease, background 0.16s ease;
	}

	.chat-row-delete:hover {
		color: var(--danger);
		background: var(--danger-wash);
	}

	.chat-row-delete :global(svg) {
		width: 1rem;
		height: 1rem;
	}

	.chat-row-num {
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		color: var(--ink-faint);
		letter-spacing: 0.04em;
	}

	.chat-row-body {
		display: grid;
		gap: 0.15rem;
		min-width: 0;
	}

	.chat-row-title,
	.chat-row-preview {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.chat-row-title {
		font-size: var(--text-sm);
		font-weight: 500;
		color: inherit;
	}

	.chat-row-preview {
		font-size: var(--text-xs);
		color: var(--ink-faint);
	}

	.chat-row-time {
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		color: var(--ink-faint);
	}

	/* ── Main column ──────────────────────────────────────────────────── */

	.chat-main {
		height: 100%;
		min-height: 0;
		min-width: 0;
		padding: var(--space-5) var(--space-6) var(--space-4);
		display: flex;
		flex-direction: column;
		overflow: hidden;
		align-self: stretch;
	}

	.main-max {
		max-width: var(--col-main);
		width: 100%;
		margin: 0 auto;
		min-width: 0;
		min-height: 0;
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		overflow: hidden;
	}

	/* ── Topbar ───────────────────────────────────────────────────────── */

	.topbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-4);
		padding-bottom: var(--space-3);
		border-bottom: 1px solid var(--rule);
		flex-shrink: 0;
	}

	.topbar-left {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		min-width: 0;
		flex: 1;
	}

	.topbar-meta {
		margin: 0;
		font-size: var(--text-xs);
		color: var(--ink-muted);
		letter-spacing: 0.04em;
		display: inline-flex;
		gap: var(--space-2);
		flex-wrap: wrap;
	}

	.topbar-sep {
		color: var(--ink-faint);
	}

	.topbar-actions {
		display: inline-flex;
		gap: var(--space-2);
		align-items: center;
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

	.text-btn {
		font-size: var(--text-sm);
		color: var(--ink-muted);
		padding: 0.55rem 0.7rem;
		border-radius: var(--radius-2);
		transition: color 0.16s ease, background 0.16s ease;
	}

	.text-btn:hover:not(:disabled) {
		color: var(--ink);
		background: var(--paper-sunk);
	}

	.text-btn:disabled {
		opacity: 0.45;
	}

	/* ── Error banner ─────────────────────────────────────────────────── */

	.banner.error {
		border-top: 2px solid var(--danger);
		background: var(--danger-wash);
		color: var(--danger);
		padding: var(--space-3) var(--space-4);
		font-size: var(--text-sm);
		flex-shrink: 0;
	}

	/* ── Thread / opening ─────────────────────────────────────────────── */

	.thread {
		flex: 1;
		min-height: 0;
		overflow: auto;
		overflow-x: hidden;
		overscroll-behavior: contain;
		-webkit-overflow-scrolling: touch;
		padding: var(--space-5) var(--space-1) var(--space-5);
	}

	.opening {
		max-width: var(--measure-prose);
		margin: var(--space-7) 0 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}

	.opening-eyebrow {
		margin: 0;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		letter-spacing: 0.08em;
		color: var(--accent);
	}

	.opening-heading {
		margin: 0;
		font-family: var(--font-prose);
		font-weight: 400;
		font-size: clamp(1.55rem, 0.55rem + 3.2vw, var(--text-2xl));
		line-height: 1.18;
		letter-spacing: -0.018em;
		color: var(--ink);
		max-width: var(--measure-display);
		font-variation-settings: 'opsz' 48;
	}

	.opening-body {
		margin: 0;
		font-size: var(--text-base);
		line-height: 1.6;
		color: var(--ink-soft);
		max-width: var(--measure-narrow);
	}

	.sample-list {
		list-style: none;
		margin: var(--space-3) 0 0;
		padding: 0;
		border-top: 1px solid var(--rule);
	}

	.sample-list li {
		border-bottom: 1px solid var(--rule);
	}

	.sample-row {
		width: 100%;
		display: grid;
		grid-template-columns: 2rem minmax(0, 1fr) 1rem;
		align-items: baseline;
		gap: var(--space-3);
		text-align: left;
		padding: var(--space-3) var(--space-2);
		color: var(--ink-soft);
		font-size: var(--text-base);
		line-height: 1.45;
		transition: color 0.16s ease, background 0.16s ease;
	}

	.sample-row:hover:not(:disabled) {
		color: var(--ink);
		background: var(--paper-sunk);
	}

	.sample-row:hover:not(:disabled) .sample-num,
	.sample-row:hover:not(:disabled) .sample-cue {
		color: var(--accent);
	}

	.sample-row:disabled {
		opacity: 0.5;
	}

	.sample-num {
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		color: var(--ink-faint);
		letter-spacing: 0.04em;
		transition: color 0.16s ease;
	}

	.sample-text {
		min-width: 0;
	}

	.sample-cue {
		font-family: var(--font-mono);
		color: var(--ink-faint);
		transition: color 0.16s ease, transform 0.16s ease;
	}

	.sample-row:hover:not(:disabled) .sample-cue {
		transform: translateX(2px);
	}

	/* ── Transcript ───────────────────────────────────────────────────── */

	.transcript {
		list-style: none;
		margin: 0;
		padding: 0;
		max-width: var(--col-transcript);
	}

	.turn {
		padding: var(--space-5) 0;
		border-top: 1px solid var(--rule);
	}

	.transcript .turn:first-child {
		padding-top: var(--space-2);
		border-top: 0;
	}

	.turn-label {
		margin: 0 0 var(--space-2);
		font-family: var(--font-mono);
		font-size: 0.7rem;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--ink-faint);
	}

	.turn.user .turn-label {
		color: var(--accent);
	}

	.question-text {
		margin: 0;
		font-family: var(--font-prose);
		font-weight: 400;
		font-size: clamp(1.05rem, 0.35rem + 1.8vw, var(--text-lg));
		line-height: 1.4;
		color: var(--ink);
		letter-spacing: -0.005em;
		max-width: var(--measure-prose);
		font-variation-settings: 'opsz' 24;
	}

	.answer-body {
		font-family: var(--font-prose);
		font-size: var(--text-md);
		line-height: 1.65;
		color: var(--ink);
		max-width: var(--measure-prose);
		font-variation-settings: 'opsz' 18;
	}

	.answer-body :global(p) {
		margin: 0 0 1em;
	}

	.answer-body :global(p:last-child) {
		margin-bottom: 0;
	}

	.answer-body :global(.cite) {
		font-family: var(--font-mono);
		font-size: 0.78em;
		color: var(--accent);
		font-weight: 500;
		padding: 0 0.05em;
		vertical-align: 0.18em;
	}

	.answer-body :global(a.cite-link) {
		text-decoration: underline;
		text-decoration-color: color-mix(in oklch, var(--accent) 45%, transparent);
		text-underline-offset: 0.14em;
		cursor: pointer;
	}

	.answer-body :global(a.cite-link:hover) {
		color: var(--accent-hover);
		text-decoration-color: var(--accent);
	}

	.answer-body :global(.md-section) {
		margin: 1em 0;
	}

	.answer-body :global(.md-section h4) {
		margin: 0 0 0.3em;
		font-family: var(--font-ui);
		font-size: var(--text-xs);
		text-transform: uppercase;
		letter-spacing: 0.12em;
		font-weight: 600;
		color: var(--ink-soft);
	}

	.answer-body :global(strong) {
		font-weight: 600;
		color: var(--ink);
	}

	.answer-body :global(em) {
		font-style: italic;
	}

	.turn.streaming.pre-answer .answer-body {
		color: var(--ink-muted);
		font-style: italic;
	}

	.sent-flash .question-text {
		animation: ink-fade 0.6s ease-out;
	}

	@keyframes ink-fade {
		from {
			opacity: 0.55;
		}
		to {
			opacity: 1;
		}
	}

	.loading-status {
		display: inline-flex;
		align-items: center;
		gap: var(--space-3);
		margin: 0;
		font-size: var(--text-sm);
		color: var(--ink-muted);
	}

	.dot-pulse {
		display: inline-flex;
		align-items: center;
		gap: 4px;
	}

	.dot-pulse span {
		width: 4px;
		height: 4px;
		border-radius: 50%;
		background: var(--ink-muted);
		opacity: 0.3;
		animation: dot-pulse 1.1s ease-in-out infinite;
	}

	.dot-pulse span:nth-child(2) {
		animation-delay: 0.18s;
	}

	.dot-pulse span:nth-child(3) {
		animation-delay: 0.36s;
	}

	@keyframes dot-pulse {
		0%, 80%, 100% {
			opacity: 0.25;
		}
		40% {
			opacity: 1;
		}
	}

	/* ── Recall strip ─────────────────────────────────────────────────── */

	.recall-strip {
		margin: 0 0 var(--space-4);
		padding: var(--space-3) 0;
		border-top: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
	}

	.recall-strip-title {
		margin: 0 0 var(--space-2);
		font-family: var(--font-mono);
		font-size: 0.7rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.recall-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		container-type: inline-size;
	}

	.recall-row {
		display: grid;
		grid-template-columns: 1rem auto minmax(0, 1fr) auto;
		gap: var(--space-2);
		align-items: baseline;
		font-size: var(--text-sm);
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-2);
		border: 1px solid var(--rule);
		background: var(--paper-sunk);
	}

	.recall-row-mark {
		font-size: 0.85rem;
		color: var(--recall-3-rule);
	}

	.recall-sev-class1 {
		border-color: color-mix(in oklch, var(--recall-1-rule) 45%, var(--rule));
		background: var(--recall-1-bg);
	}

	.recall-sev-class1 .recall-row-mark {
		color: var(--recall-1-fg);
	}

	.recall-sev-class1 .recall-row-label {
		color: var(--recall-1-fg);
		font-weight: 700;
	}

	.recall-sev-class2 {
		border-color: color-mix(in oklch, var(--recall-2-rule) 40%, var(--rule));
		background: var(--recall-2-bg);
	}

	.recall-sev-class2 .recall-row-mark,
	.recall-sev-class2 .recall-row-label {
		color: var(--recall-2-fg);
	}

	.recall-sev-class3 {
		border-color: color-mix(in oklch, var(--recall-3-rule) 50%, var(--rule));
		background: var(--recall-3-bg);
	}

	.recall-sev-class3 .recall-row-mark,
	.recall-sev-class3 .recall-row-label {
		color: var(--recall-3-fg);
	}

	.recall-row-label {
		font-weight: 600;
		letter-spacing: -0.005em;
	}

	.recall-row-drug {
		color: var(--ink);
		text-transform: capitalize;
		font-weight: 500;
	}

	.recall-row-link {
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		color: var(--accent);
	}

	@container (max-width: 28rem) {
		.recall-row {
			grid-template-columns: 1rem minmax(0, 1fr);
			row-gap: 0.1rem;
		}

		.recall-row-drug {
			grid-column: 2;
		}

		.recall-row-link {
			grid-column: 1 / -1;
			padding-left: calc(1rem + var(--space-2));
		}
	}

	/* ── Composer ─────────────────────────────────────────────────────── */

	.composer {
		flex-shrink: 0;
		padding: var(--space-3) 0 max(0px, env(safe-area-inset-bottom));
		border-top: 1px solid var(--rule);
	}

	.composer-form {
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.composer-main {
		display: flex;
		align-items: stretch;
		gap: var(--space-3);
	}

	textarea {
		flex: 1;
		min-width: 0;
		min-height: 3.25rem;
		resize: none;
		outline: none;
		padding: 0.65rem 0.85rem;
		background: var(--paper);
		color: var(--ink);
		font-family: var(--font-ui);
		font-size: max(16px, var(--text-base));
		line-height: 1.45;
		border: 1px solid var(--rule);
		border-radius: var(--radius-2);
		transition: border-color 0.16s ease;
		max-height: min(30dvh, 12rem);
	}

	textarea:focus {
		border-color: var(--ink);
	}

	textarea::placeholder {
		color: var(--ink-faint);
	}

	.send {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0 var(--space-4);
		min-height: 2.6rem;
		font-size: var(--text-sm);
		font-weight: 500;
		color: var(--paper);
		background: var(--ink);
		border: 1px solid var(--ink);
		border-radius: var(--radius-2);
		transition: background 0.16s ease, color 0.16s ease, border-color 0.16s ease;
	}

	.send:hover:not(:disabled) {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--paper);
	}

	.send:disabled {
		background: var(--paper-sunk);
		color: var(--ink-faint);
		border-color: var(--rule);
	}

	.send-cue {
		font-family: var(--font-mono);
		opacity: 0.7;
	}

	.composer-hint {
		margin: 0;
		font-size: var(--text-xs);
		color: var(--ink-faint);
		letter-spacing: 0.02em;
	}

	/* ── Dev panels (back room) ───────────────────────────────────────── */

	.meta-row {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-3);
		flex-shrink: 0;
	}

	.meta-card,
	.detail-panel {
		border: 1px solid var(--rule);
		border-radius: var(--radius-2);
		padding: var(--space-3) var(--space-4);
		background: var(--paper-sunk);
	}

	.meta-card summary,
	.detail-panel summary {
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--ink-soft);
		font-weight: 500;
	}

	.metric-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: var(--space-2);
		margin-top: var(--space-3);
	}

	.metric-grid div {
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-2);
		background: var(--paper);
		border: 1px solid var(--rule);
		display: grid;
		gap: 0.15rem;
	}

	.metric-grid strong {
		font-size: var(--text-base);
		overflow-wrap: anywhere;
	}

	.metric-grid span {
		color: var(--ink-muted);
		font-size: var(--text-xs);
	}

	.details-stack {
		min-width: 0;
		flex-shrink: 0;
	}

	.details-stack-summary {
		list-style: none;
		cursor: pointer;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		font-weight: 500;
		color: var(--ink-soft);
		padding: var(--space-3) var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-2);
		background: var(--paper-sunk);
	}

	.details-stack-summary::-webkit-details-marker {
		display: none;
	}

	.details-stack-body {
		display: grid;
		gap: var(--space-2);
		margin-top: var(--space-3);
		max-height: 34dvh;
		overflow: auto;
	}

	@media (max-width: 640px) {
		.details-stack-body {
			max-height: min(50dvh, 22rem);
		}
	}

	@media (min-width: 641px) {
		.details-stack[open] .details-stack-summary {
			display: none;
		}

		.details-stack[open] .details-stack-body {
			margin-top: 0;
		}
	}

	.split {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-3);
	}

	pre {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		border-radius: var(--radius-2);
		padding: var(--space-3);
		color: var(--ink-soft);
		background: var(--paper);
		border: 1px solid var(--rule);
		font-size: var(--text-xs);
	}

	.note {
		font-size: var(--text-sm);
		margin: 0;
	}

	.ok,
	.ok-text {
		color: var(--success);
	}

	.warn,
	.warn-text {
		color: var(--warning);
	}

	.muted {
		color: var(--ink-muted);
	}

	.pill-list,
	.metadata {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	.data-pill,
	.metadata span {
		border: 1px solid var(--rule);
		background: var(--paper);
		padding: 0.25rem 0.55rem;
		border-radius: var(--radius-2);
		font-size: var(--text-xs);
	}

	.data-pill {
		display: grid;
		gap: 0.1rem;
	}

	.data-pill strong {
		font-size: var(--text-sm);
	}

	.data-pill small {
		color: var(--ink-muted);
	}

	.ingest-box,
	.source-card {
		border-radius: var(--radius-2);
		padding: var(--space-3);
		background: var(--paper);
		border: 1px solid var(--rule);
		margin-top: var(--space-3);
		font-size: var(--text-sm);
	}

	.source-list {
		display: grid;
		gap: var(--space-2);
	}

	.source-head {
		display: flex;
		justify-content: space-between;
		gap: var(--space-4);
		font-size: var(--text-sm);
	}

	.score {
		font-family: var(--font-mono);
		color: var(--accent);
		font-size: var(--text-xs);
	}

	.metadata {
		margin-top: var(--space-2);
	}

	.metadata span {
		color: var(--ink-muted);
	}

	.status-list {
		margin: var(--space-2) 0 0;
		padding-left: 1.25rem;
		color: var(--ink-soft);
		font-size: var(--text-sm);
	}

	.status-list li + li {
		margin-top: 0.3rem;
	}

	.timing-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
		gap: 0.4rem;
		margin-top: var(--space-3);
	}

	.timing-grid div {
		display: flex;
		justify-content: space-between;
		gap: var(--space-3);
		padding: 0.4rem 0.55rem;
		border-radius: var(--radius-2);
		background: var(--paper);
		border: 1px solid var(--rule);
		font-size: var(--text-xs);
	}

	.composer-footer {
		display: flex;
		align-items: flex-start;
		gap: var(--space-2);
		margin-top: var(--space-3);
	}

	.composer-drawer {
		position: relative;
	}

	.composer-drawer summary {
		list-style: none;
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-muted);
		padding: 0.35rem 0.6rem;
		border: 1px solid var(--rule);
		border-radius: var(--radius-2);
		cursor: pointer;
	}

	.composer-drawer summary::-webkit-details-marker {
		display: none;
	}

	.composer-drawer[open] summary {
		border-color: var(--accent);
		color: var(--accent);
	}

	.control-grid {
		position: absolute;
		bottom: calc(100% + var(--space-2));
		left: 0;
		z-index: 10;
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--space-3);
		width: min(26rem, 82vw);
		max-height: min(50dvh, 24rem);
		overflow-y: auto;
		border: 1px solid var(--rule-strong);
		background: var(--paper);
		padding: var(--space-3);
		border-radius: var(--radius-2);
		font-size: var(--text-sm);
	}

	.control-grid label {
		display: grid;
		gap: 0.3rem;
		color: var(--ink-soft);
	}

	.control-grid output {
		font-family: var(--font-mono);
		color: var(--accent);
		font-weight: 500;
		font-size: var(--text-xs);
	}

	input[type='range'] {
		accent-color: var(--accent);
	}

	input[type='checkbox'] {
		width: 0.95rem;
		height: 0.95rem;
		accent-color: var(--accent);
	}

	.check-row {
		grid-template-columns: auto 1fr !important;
		align-items: center;
	}

	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}

	/* ── Responsive ───────────────────────────────────────────────────── */

	@media (max-width: 920px) {
		.mobile-overlay-backdrop {
			display: block;
			position: fixed;
			inset: 0;
			z-index: 35;
			margin: 0;
			padding: 0;
			border: 0;
			border-radius: 0;
			background: oklch(0.18 0.012 260 / 0.35);
			cursor: pointer;
			-webkit-tap-highlight-color: transparent;
		}

		.app-shell,
		.app-shell.rail-minimized {
			display: flex;
			flex-direction: column;
			height: 100dvh;
			max-height: 100dvh;
			min-height: 100dvh;
			overflow: hidden;
		}

		.chat-main {
			flex: 1;
			min-height: 0;
			height: auto;
			padding: var(--space-4);
			padding-left: max(var(--space-4), env(safe-area-inset-left));
			padding-right: max(var(--space-4), env(safe-area-inset-right));
			padding-top: max(var(--space-4), env(safe-area-inset-top));
			padding-bottom: max(var(--space-4), env(safe-area-inset-bottom));
		}

		.session-rail {
			position: fixed;
			z-index: 40;
			top: 0;
			bottom: 0;
			left: 0;
			width: min(20rem, 90vw);
			max-width: 100%;
			height: 100dvh;
			max-height: 100dvh;
			margin: 0;
			border-right: 1px solid var(--rule);
			border-top: none;
			box-shadow: none;
			transform: translate3d(-100%, 0, 0);
			transition: transform 0.22s cubic-bezier(0.22, 0.61, 0.36, 1);
			overflow-y: auto;
			overflow-x: hidden;
			overscroll-behavior: contain;
			-webkit-overflow-scrolling: touch;
			padding-top: max(var(--space-5), env(safe-area-inset-top));
			padding-left: max(var(--space-4), env(safe-area-inset-left));
			padding-bottom: max(var(--space-4), env(safe-area-inset-bottom));
			background: var(--paper);
		}

		.app-shell:not(.rail-minimized) .session-rail {
			transform: translate3d(0, 0, 0);
		}

		@media (prefers-reduced-motion: reduce) {
			.session-rail {
				transition: none;
			}
		}

		.composer.composer-drawer-active {
			position: relative;
			z-index: 45;
		}

		.composer-drawer[open] .control-grid {
			z-index: 46;
		}

		.meta-row,
		.split,
		.control-grid {
			grid-template-columns: 1fr;
		}

		.topbar {
			align-items: flex-start;
		}

		.opening {
			margin-top: var(--space-5);
		}

		.opening-heading {
			font-size: var(--text-xl);
		}
	}

	@media (max-width: 640px) {
		.chat-main {
			padding: var(--space-3) var(--space-3) max(var(--space-3), env(safe-area-inset-bottom));
		}

		.main-max {
			gap: var(--space-3);
		}

		.composer-main {
			flex-direction: column;
			align-items: stretch;
		}

		.send {
			min-height: 2.8rem;
			justify-content: center;
		}

		.opening-heading {
			font-size: 1.6rem;
			line-height: 1.15;
			font-variation-settings: 'opsz' 32;
		}

		.question-text {
			font-size: var(--text-md);
		}

		.sample-row {
			grid-template-columns: 1.5rem minmax(0, 1fr) 0.75rem;
			padding: var(--space-3) 0;
			font-size: var(--text-sm);
		}

		.turn {
			padding: var(--space-4) 0;
		}

		.turn-label,
		.opening-eyebrow {
			font-size: 0.72rem;
			letter-spacing: 0.12em;
		}

		.transcript {
			max-width: none;
		}
	}

	/* Laptop — 1280–1599: widen main column so the editorial measure breathes */
	@media (min-width: 1280px) {
		.chat-main {
			padding: var(--space-6) var(--space-7) var(--space-5);
		}

		.main-max {
			max-width: var(--col-main-wide);
			gap: var(--space-5);
		}

		.transcript {
			max-width: var(--col-transcript-wide);
		}
	}

	/* Large desktop — push wordmark and brand-mark up a half-step, widen slightly more */
	@media (min-width: 1600px) {
		.main-max {
			max-width: var(--col-main-xwide);
		}

		.brand-mark {
			width: 2.4rem;
			height: 2.4rem;
		}

		.brand-wordmark {
			font-size: 1.15rem;
		}

		.opening-heading {
			font-size: 2.75rem;
			font-variation-settings: 'opsz' 60;
		}
	}

	/* 4K and up — bump root size so content isn't postage-stamp small */
	@media (min-width: 1920px) {
		:root {
			font-size: 17px;
		}
	}

	/* Coarse pointer — escalate every chrome control to the 44px minimum target */
	@media (pointer: coarse) {
		.icon-btn,
		.rail-toggle,
		.chat-row-delete {
			min-width: var(--tap);
			min-height: var(--tap);
			width: var(--tap);
			height: var(--tap);
		}

		.text-btn {
			min-height: var(--tap);
			padding: 0.7rem 0.9rem;
		}

		.composer-drawer summary {
			min-height: var(--tap);
			display: inline-flex;
			align-items: center;
		}
	}
</style>
