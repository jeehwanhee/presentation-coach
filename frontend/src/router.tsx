import { createBrowserRouter } from "react-router-dom";
import { CreateScreen } from "./features/create/CreateScreen";
import { UploadScreen } from "./features/upload/UploadScreen";
import { ReportScreen } from "./features/report/ReportScreen";

export const router = createBrowserRouter([
  { path: "/", element: <CreateScreen /> },
  { path: "/upload", element: <UploadScreen /> },
  { path: "/r/:id", element: <ReportScreen /> },
]);
