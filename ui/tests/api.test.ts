import test from 'node:test'
import assert from 'node:assert/strict'
import { host } from '../src/api.ts'

test('an injected but unfinished pywebview API waits for the ready event', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const browser = Object.assign(new EventTarget(), {pywebview: {api: {}}})
  Object.defineProperty(globalThis, 'window', {value: browser, configurable: true})
  try {
    let settled = false
    const pending = host().then(api => {settled = true; return api})
    await Promise.resolve()
    assert.equal(settled, false)
    Object.assign(browser.pywebview.api, {command: () => {}, ready: () => {}})
    browser.dispatchEvent(new Event('pywebviewready'))
    assert.equal(await pending, browser.pywebview.api)
  } finally {
    if (previous) Object.defineProperty(globalThis, 'window', previous)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})
