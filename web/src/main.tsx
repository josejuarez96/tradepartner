import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/newsreader";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./tokens/tokens.css";
import "./styles/base.css";
import { App } from "./App";
import { applyTheme, initialTheme } from "./components/ThemeSwitch";

applyTheme(initialTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
