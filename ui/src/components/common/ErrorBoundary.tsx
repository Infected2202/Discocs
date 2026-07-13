import { Component, type ReactNode } from "react"
import { withTranslation, type WithTranslation } from "react-i18next"
import { AlertTriangle, RotateCcw } from "lucide-react"

interface Props extends WithTranslation {
  children: ReactNode
}

interface State {
  hasError: boolean
  message: string
}

class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: "" }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message }
  }

  handleRetry = () => {
    this.setState({ hasError: false, message: "" })
  }

  render() {
    if (this.state.hasError) {
      const { t } = this.props
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 px-6 text-center">
          <AlertTriangle size={40} className="text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">{t("status.error")}</p>
            <p className="text-xs text-muted-foreground mt-1 max-w-sm truncate">
              {this.state.message}
            </p>
          </div>
          <button
            onClick={this.handleRetry}
            className="flex items-center gap-2 rounded-lg bg-muted px-4 py-2 text-sm font-medium hover:bg-muted/70 transition-colors"
          >
            <RotateCcw size={14} />
            {t("actions.tryAgain")}
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

export default withTranslation("common")(ErrorBoundary)
