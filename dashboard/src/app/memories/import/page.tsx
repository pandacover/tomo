import { MemoryImportDropzone } from "@/components/import/MemoryImportDropzone";
import { TopBar } from "@/components/shell/TopBar";
import { glossary } from "@/domain/glossary";

export default function ImportMemoryPage() {
  return (
    <>
      <TopBar
        crumbs={[
          { label: "memories", href: "/memories" },
          { label: "import memory" },
        ]}
        pageTitle={glossary.importMemory.title}
      />
      <MemoryImportDropzone />
    </>
  );
}