// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import starlight from '@astrojs/starlight';

const site = process.env.DOCS_SITE_URL || 'https://docs.photobox.app';

export default defineConfig({
	site,
	output: 'static',
	trailingSlash: 'always',
	integrations: [
		starlight({
			title: 'PhotoBox Docs',
			description:
				'Architecture, security, ingestion, delivery, and deployment documentation for the PhotoBox API platform.',
			tagline: 'Unified vault architecture for modern photography delivery.',
			favicon: '/favicon.svg',
			head: [
				{
					tag: 'meta',
					attrs: {
						name: 'theme-color',
						content: '#0f172a',
					},
				},
				{
					tag: 'meta',
					attrs: {
						property: 'og:site_name',
						content: 'PhotoBox Docs',
					},
				},
			],
			tableOfContents: {
				minHeadingLevel: 2,
				maxHeadingLevel: 4,
			},
			customCss: ['/src/styles/custom.css'],
			lastUpdated: true,
			credits: false,
			sidebar: [
				{
					label: 'Foundations',
					items: [
						{ label: 'Architecture Overview', slug: 'foundations/architecture-overview' },
						{ label: 'System Components', slug: 'foundations/system-components' },
						{ label: 'Data Model', slug: 'foundations/data-model' },
					],
				},
				{
					label: 'Pipelines',
					items: [
						{ label: 'Upload Pipelines', slug: 'pipelines/upload-pipelines' },
						{ label: 'Delivery Layer', slug: 'pipelines/delivery-layer' },
						{ label: 'Webhook System', slug: 'pipelines/webhook-system' },
						{ label: 'Notification System', slug: 'pipelines/notification-system' },
					],
				},
				{
					label: 'Security',
					items: [{ label: 'Security Architecture', slug: 'security/security-architecture' }],
				},
				{
					label: 'Operations',
					items: [
						{ label: 'API Reference', slug: 'operations/api-reference' },
						{ label: 'Infrastructure & Configuration', slug: 'operations/infrastructure-configuration' },
						{ label: 'Testing', slug: 'operations/testing' },
						{ label: 'Deployment', slug: 'operations/deployment' },
					],
				},
			],
		}),
		sitemap(),
	],
});