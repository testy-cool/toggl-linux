import { createRoot } from "react-dom/client";
import "./theme.css";
import WorkApp from "./WorkApp.tsx";

createRoot(document.getElementById("root")!).render(<WorkApp />);
