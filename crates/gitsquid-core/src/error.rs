pub type Result<T> = std::result::Result<T, Error>;

/// Every failure the interface can be asked to display. The message is the whole payload:
/// it is written for the person reading it, not for a caller matching on a variant.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum Error {
    /// git refused the operation, or the request never reached it.
    #[error("{0}")]
    Git(String),
    /// The path is not a repository, or the configuration cannot be read.
    #[error("{0}")]
    Config(String),
}

impl Error {
    pub fn git(message: impl Into<String>) -> Self {
        Self::Git(message.into())
    }

    pub fn config(message: impl Into<String>) -> Self {
        Self::Config(message.into())
    }
}

impl From<git2::Error> for Error {
    fn from(error: git2::Error) -> Self {
        Self::Git(error.message().to_string())
    }
}
