import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { bootDisplayPreferences } from './theme'
import './styles.css'

// Before createRoot, never in an effect: an effect runs after the first
// commit, which is one painted frame of the wrong accent. Everything
// accent-coloured lives inside #root and #root is empty until render() below,
// so no inline <head> script is needed.
//
// This holds ONLY while --ink stays a background token: styles.css paints
// <body> before this module runs, and body{background:var(--ink)} is
// byte-identical in all twelve themes. If a [data-theme] block ever redefined
// --ink the body would paint a neon colour full-screen and then snap. No theme
// block may name it.
bootDisplayPreferences()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
