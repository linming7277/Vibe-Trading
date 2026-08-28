import { fireEvent, render, screen } from "@testing-library/react";
import { LazyDetails } from "../LazyDetails";

describe("LazyDetails", () => {
  it("mounts content only when first expanded and keeps it mounted after closing", () => {
    const renderContent = vi.fn();
    function DeferredContent() { renderContent(); return <div>延迟内容</div>; }
    render(<LazyDetails summary="展开资料"><DeferredContent /></LazyDetails>);

    expect(screen.queryByText("延迟内容")).not.toBeInTheDocument();
    const details = screen.getByText("展开资料").closest("details") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(screen.getByText("延迟内容")).toBeInTheDocument();
    expect(renderContent).toHaveBeenCalledTimes(1);

    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(screen.getByText("延迟内容")).toBeInTheDocument();
    expect(renderContent).toHaveBeenCalledTimes(1);
  });
});
