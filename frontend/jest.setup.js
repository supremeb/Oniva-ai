import '@testing-library/jest-dom'

global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

window.HTMLElement.prototype.scrollIntoView = function() {}
