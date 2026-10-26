<script setup lang="ts">
import { computed, ref } from 'vue'
import { alertPermission } from '@/lib/alerts'
import CopyButton from '@/components/base/CopyButton.vue'
import TagChip from '@/components/base/TagChip.vue'

/** Step-by-step setup for desktop alerts: the page, the browser, and macOS, checked where possible. */
const origin = window.location.origin
const host = window.location.hostname
const permission = ref(alertPermission())
const secure = window.isSecureContext
// Chrome treats https and this machine (localhost, 127.0.0.1) as secure; other http addresses
// need the "Insecure origins treated as secure" flag before notifications work.
const local = ['localhost', '127.0.0.1', '[::1]'].includes(host) || host.endsWith('.localhost')
const needsFlag = !secure && !local

const FLAG_URL = 'chrome://flags/#unsafely-treat-insecure-origin-as-secure'
const SITE_URL = computed(() => `chrome://settings/content/siteDetails?site=${encodeURIComponent(origin)}`)
const PLAIN_TEST = "setTimeout(() => new Notification('Console test', { body: 'Plain browser notification' }), 5000)"
const plainResult = ref('')

function refresh() {
  permission.value = alertPermission()
}

/** The same plain notification as the console test, so the browser and macOS can be checked apart from Alfred. */
function plainTest() {
  refresh()
  if (permission.value !== 'granted') {
    plainResult.value = 'Notifications are not allowed for this page yet; see step 1.'
    return
  }
  plainResult.value = 'Sending in 5 seconds: switch to another tab or app now.'
  window.setTimeout(() => {
    try {
      const notification = new window.Notification('Plain browser test', { body: 'If you can read this, the browser and macOS are set up.' })
      notification.addEventListener('show', () => {
        plainResult.value = 'The browser handed the alert to macOS. Nothing on screen means macOS is hiding it: see steps 3 and 4.'
      })
      notification.addEventListener('error', () => {
        plainResult.value = 'The browser could not show it: check steps 1 and 2.'
      })
    } catch (error) {
      plainResult.value = `The browser refused it: ${String(error)}`
    }
  }, 5000)
}

const permissionTone = computed(() => (permission.value === 'granted' ? 'ok' : permission.value === 'default' ? 'brass' : 'danger'))
const permissionLabel = computed(
  () => ({ granted: 'Allowed', default: 'Not asked yet', denied: 'Blocked', unsupported: 'Not supported' })[permission.value],
)
</script>

<template>
  <details class="help" @toggle="refresh">
    <summary>Not seeing alerts? Set up your browser and Mac</summary>
    <div class="body">
      <p class="intro">
        An alert passes through three gates: this page's permission, the browser, and macOS. Each one can drop it without an
        error, so check them in order. Use <strong>Send a test</strong> after each change, and switch to another tab within 5
        seconds; alerts are meant for when Alfred is in the background.
      </p>

      <ol class="steps">
        <li>
          <h4>Allow notifications for this page <TagChip :tone="permissionTone">{{ permissionLabel }}</TagChip></h4>
          <p>
            Turn on <strong>Desktop alerts</strong> above and choose <strong>Allow</strong> when the browser asks. If it was blocked,
            click the icon left of the address bar, set <strong>Notifications</strong> to <strong>Allow</strong>, and reload. In
            Chrome you can also open the site's settings:
          </p>
          <p class="code-line"><code>{{ SITE_URL }}</code><CopyButton :text="SITE_URL" label="Copy site settings address" /></p>
        </li>

        <li>
          <h4>
            Let Chrome treat this address as secure
            <TagChip v-if="needsFlag" tone="brass">Needed for {{ host }}</TagChip>
            <TagChip v-else tone="ok">Not needed here</TagChip>
          </h4>
          <p>
            Browsers only allow notifications on <code>https</code> pages and on this machine (<code>localhost</code>,
            <code>127.0.0.1</code>). For any other <code>http</code> address, such as a shared server, open the flag below, add the
            address (several are separated by commas, without spaces), choose <strong>Enabled</strong>, and click
            <strong>Relaunch</strong>.
          </p>
          <p class="code-line"><code>{{ FLAG_URL }}</code><CopyButton :text="FLAG_URL" label="Copy flag address" /></p>
          <p class="code-line"><span class="faint small">This page:</span> <code>{{ origin }}</code><CopyButton :text="origin" label="Copy this address" /></p>
        </li>

        <li>
          <h4>Allow the browser in macOS</h4>
          <p>
            Open <strong>System Settings › Notifications</strong> and select <strong>Google Chrome</strong> (or your browser). Turn on
            <strong>Allow notifications</strong> and choose <strong>Banners</strong> (they slide away) or <strong>Alerts</strong>
            (they stay until dismissed). If there is also a <strong>Google Chrome (Alerts)</strong> entry, allow it too.
          </p>
        </li>

        <li>
          <h4>Turn off Focus</h4>
          <p>
            A Focus mode such as <strong>Do Not Disturb</strong> silences every banner. Open <strong>Control Center</strong> in the
            menu bar and switch Focus off, or allow Google Chrome under <strong>System Settings › Focus</strong>.
          </p>
        </li>

        <li>
          <h4>Check Notification Center</h4>
          <p>
            Click the date and time at the top right of the screen. Alerts listed there arrived but were not shown as banners:
            go back to step 3 and pick <strong>Banners</strong> or <strong>Alerts</strong>.
          </p>
        </li>

        <li>
          <h4>Compare with a plain browser notification</h4>
          <p>
            This sends the simplest possible notification, without Alfred. If it shows and Alfred's test does not, report it as an
            Alfred bug; if neither shows, the cause is in steps 1 to 4.
          </p>
          <p class="row">
            <button type="button" class="test" @click="plainTest">Send a plain test</button>
            <span v-if="plainResult" class="small" role="status">{{ plainResult }}</span>
          </p>
          <p class="faint small">Or paste this in the browser console (⌥⌘J), then switch tabs:</p>
          <p class="code-line"><code>{{ PLAIN_TEST }}</code><CopyButton :text="PLAIN_TEST" label="Copy console test" /></p>
        </li>
      </ol>
    </div>
  </details>
</template>

<style scoped>
.help {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--paper);
}

summary {
  padding: 8px 12px;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--accent);
  cursor: pointer;
}

.body {
  padding: 0 14px 12px;
  font-size: var(--text-sm);
  line-height: 1.5;
}

.intro {
  margin: 0 0 8px;
  color: var(--ink-2);
}

.steps {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 0 0 2px;
  font-size: var(--text-sm);
}

p {
  margin: 4px 0;
}

code {
  font-family: var(--font-mono);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.code-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.code-line code {
  padding: 2px 6px;
  border-radius: var(--radius-s);
  background: var(--panel-2);
}

.row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.test {
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--ink);
  padding: 4px 10px;
  font-size: var(--text-sm);
  cursor: pointer;
}

.test:hover {
  border-color: var(--accent);
}
</style>
