from __future__ import annotations

from typing import Any, Generic, Optional, Tuple, TypeVar, Union, cast

import multimethod
import numpy as np
import scipy
from sklearn.utils.validation import check_is_fitted

from ..._utils._sklearn_adapter import BaseEstimator
from ...misc.regularization import L2Regularization, compute_penalty_matrix
from ...representation import FData, FDataGrid
from ...representation.basis import Basis, FDataBasis, _GridBasis
from ...typing._numpy import NDArrayFloat

INV_EPS = 1e-15


def _power_solver(
    X: NDArrayFloat,
    tol: float,
    max_iter: int,
) -> NDArrayFloat:
    """Return the dominant eigenvector of a matrix using the power method."""
    t = X[:, 0]
    t_prev = np.ones(t.shape) * np.max(t) * 2
    iter_count = 0
    while np.linalg.norm(t - t_prev) > tol:
        t_prev = t
        t = X @ t
        t /= np.linalg.norm(t)
        iter_count += 1
        if iter_count > max_iter:
            break
    return t


def _calculate_weights(
    X: NDArrayFloat,
    Y: NDArrayFloat,
    G_ww: NDArrayFloat,
    G_xw: NDArrayFloat,
    G_cc: NDArrayFloat,
    G_yc: NDArrayFloat,
    L_X_inv: NDArrayFloat,
    L_Y_inv: NDArrayFloat,
    tol: float,
    max_iter: int,
) -> Tuple[NDArrayFloat, NDArrayFloat]:
    """
    Calculate the weights for the PLS algorithm.

    Parameters:
        - X: (n_samples, n_features)
            The X block data matrix.
        - Y: (n_samples, n_targets)
            The Y block data matrix.
        - n_components: number of components to extract.

        - G_ww: (n_features, n_features)
            The inner product matrix for the X block weights
            (The discretization weights matrix in the case of FDataGrid).
        - G_xw: (n_features, n_features)
            The inner product matrix for the X block data and weights
            (The discretization weights matrix in the case of FDataGrid).

        - G_cc: (n_targets, n_targets)
            The inner product matrix for the Y block weights
            (The discretization weights matrix in the case of FDataGrid).
        - G_yc: (n_targets, n_targets)
            The inner product matrix for the Y block data and weights
            (The discretization weights matrix in the case of FDataGrid).

        - L_X_inv: (n_features, n_features)
            The inverse of the Cholesky decomposition:
            L_X @ L_X.T = G_ww + P_x,
            where P_x is a the penalty matrix for the X block.
        - L_Y_inv: (n_targets, n_targets)
            The inverse of the Cholesky decomposition:
            L_Y @ L_Y.T = G_cc + P_y,
            where P_y is a the penalty matrix for the Y block.

        - tol: The tolerance for the power method.
        - max_iter: The maximum number of iterations for the power method.
    Returns:
        - w: (n_features, 1)
            The X block weights.
        - c: (n_targets, 1)
            The Y block weights.
    """
    X = X @ G_xw @ L_X_inv.T
    Y = Y @ G_yc @ L_Y_inv.T
    S = X.T @ Y
    w = _power_solver(
        S @ S.T,
        tol=tol,
        max_iter=max_iter,
    )

    # Calculate the other weight
    c = np.dot(Y.T, np.dot(X, w))

    # Undo the transformation
    w = L_X_inv.T @ w

    # Normalize
    w /= np.sqrt(np.dot(w.T, G_ww @ w)) + INV_EPS

    # Undo the transformation
    c = L_Y_inv.T @ c

    # Normalize the other weight
    c /= np.sqrt(np.dot(c.T, G_cc @ c)) + INV_EPS

    return w, c


BlockType = TypeVar(
    "BlockType",
    bound=Union[FDataGrid, FDataBasis, NDArrayFloat],
)


# Ignore too many public instance attributes
class _FPLSBlock(Generic[BlockType]):  # noqa: WPS230
    """
    Class to store the data of a block of a FPLS model.

    This is an internal class, intended to be used only by the FPLS class.
    It provides a common interface for the different types of blocks,
    simplifying the implementation of the FPLS algorithm.

    There are three types of blocks (depending on BlockType):
    mutltivariate (NDArrayFloat), basis (FDataBasis) and grid (FDataGrid).

    In the following, n_samples is the number of samples of the block.
    n_features is:
        - The number of features of the block in the case of multivariate
          block.
        - The number of basis functions in the case of a FDataBasis block.
        - The number of points in the case of a FDataGrid block.

    Parameters:
        - data: The data of the block.
        - n_components: Number of components to extract.
        - label: Label of the block (X or Y).
        - integration_weights: Array with shape (n_features,).
            The integration weights of the block. It must be None for
            multivariate or FDataBasis blocks.
        - regularization: The regularization to apply to the block.
            It must be None for multivariate blocks.
        - weights_basis: The basis of the weights. It must be None for
            multivariate or grid blocks.

    Attributes:
        - n_components: Number of components to extract.
        - label: Label of the block (X or Y).
        - data: The data of the block.
        - data_matrix: (n_samples, n_features) matrix. The data
          matrix of the block.
        - mean: The mean of the data.
        - weights_basis: The basis of the weights.
        - regularization_matrix: (n_features, n_features) matrix.
            Inner product matrix of the regularization operator applied
            to the basis or the grid.
        - integration_matrix: (n_features, n_features) matrix.
            Diagonal matrix of the integration weights.
        - G_data_weights: (n_samples, n_samples) matrix.  The inner
            product matrix for the data and weights
            (The discretization matrix in grid blocks).
        - G_weights: (n_samples, n_samples) matrix.  The inner product
            matrix for the weights (The discretization matrix in grid blocks).

        - rotations: (n_features, n_components) matrix.  The
            rotations of the block.
        - loadings_matrix: (n_features, n_components) matrix.  The
            loadings of the block.
        - loadings: The loadings of the block (same type as the data).

    Examples:
        Fit a FPLS model with two components.

        >>> from skfda.preprocessing.dim_reduction import FPLS
        >>> from skfda.datasets import fetch_tecator
        >>> from skfda.representation import FDataGrid
        >>> from skfda.typing._numpy import NDArrayFloat
        >>> X, y = fetch_tecator(return_X_y=True)
        >>> fpls = FPLS[FDataGrid, NDArrayFloat](n_components=2)
        >>> fpls = fpls.fit(X, y)

    """

    mean: BlockType

    def __init__(
        self,
        data: BlockType,
        n_components: int,
        label: str,
        integration_weights: NDArrayFloat | None,
        regularization: L2Regularization[Any] | None,
        weights_basis: Basis | None,
    ) -> None:
        self.n_components = n_components
        self.label = label
        self._initialize_data(
            data=data,
            integration_weights=integration_weights,
            regularization=regularization,
            weights_basis=weights_basis,
        )

    @multimethod.multidispatch
    def _initialize_data(
        self,
        data: Union[FData, NDArrayFloat],
        integration_weights: Optional[NDArrayFloat],
        regularization: Optional[L2Regularization[Any]],
        weights_basis: Optional[Basis],
    ) -> None:
        """Initialize the data of the block."""
        raise NotImplementedError(
            f"Data type {type(data)} or combination of arguments"
            "not supported",
        )

    @_initialize_data.register
    def _initialize_data_multivariate(
        self,
        data: np.ndarray,  # type: ignore[type-arg]
        integration_weights: None,
        regularization: None,
        weights_basis: None,
    ) -> None:
        """Initialize the data of a multivariate block."""
        if len(data.shape) == 1:
            data = data[:, np.newaxis]

        self.G_data_weights = np.identity(data.shape[1])
        self.G_weights = np.identity(data.shape[1])

        self.mean = np.mean(data, axis=0)
        self.data_matrix = data - self.mean
        self.data = data - self.mean

        self.regularization_matrix = np.zeros(
            (data.shape[1], data.shape[1]),
        )

    @_initialize_data.register
    def _initialize_data_grid(
        self,
        data: FDataGrid,
        integration_weights: Optional[np.ndarray],  # type: ignore[type-arg]
        regularization: Optional[L2Regularization[Any]],
        weights_basis: None,
    ) -> None:
        """Initialize the data of a grid block."""
        self.mean = data.mean()
        self.data = data - self.mean
        self.data_matrix = data.data_matrix[..., 0]

        # Arrange the integration weights in a diagonal matrix
        # By default, use Simpson's rule
        if integration_weights is None:
            identity = np.identity(self.data_matrix.shape[1])
            integration_weights = scipy.integrate.simps(
                identity,
                self.data.grid_points[0],
            )
        self.integration_weights = np.diag(integration_weights)

        # Compute the regularization matrix
        # By default, all zeros (no regularization)
        regularization_matrix = None
        if regularization is not None:
            regularization_matrix = compute_penalty_matrix(
                basis_iterable=(
                    _GridBasis(grid_points=self.data.grid_points),
                ),
                regularization_parameter=1,
                regularization=regularization,
            )
        if regularization_matrix is None:
            regularization_matrix = np.zeros(
                (self.data_matrix.shape[1], self.data_matrix.shape[1]),
            )

        self.regularization_matrix = regularization_matrix
        self.G_data_weights = self.integration_weights
        self.G_weights = self.integration_weights

    @_initialize_data.register
    def _initialize_data_basis(
        self,
        data: FDataBasis,
        integration_weights: None,
        regularization: Optional[L2Regularization[Any]],
        weights_basis: Optional[Basis],
    ) -> None:
        """Initialize the data of a basis block."""
        self.mean = data.mean()
        self.data = data - self.mean
        self.data_matrix = self.data.coefficients

        # By default, use the basis of the input data
        # for the weights
        if weights_basis is None:
            self.weights_basis = data.basis
        else:
            self.weights_basis = weights_basis

        # Compute the regularization matrix
        # By default, all zeros (no regularization)
        regularization_matrix = None
        if regularization is not None:
            regularization_matrix = compute_penalty_matrix(
                basis_iterable=(self.weights_basis,),
                regularization_parameter=1,
                regularization=regularization,
            )
        if regularization_matrix is None:
            regularization_matrix = np.zeros(
                (self.data_matrix.shape[1], self.data_matrix.shape[1]),
            )

        self.regularization_matrix = regularization_matrix
        self.G_weights = self.weights_basis.gram_matrix()
        self.G_data_weights = self.data.basis.inner_product_matrix(
            self.weights_basis,
        )

    def set_nipals_results(
        self,
        rotations: NDArrayFloat,
        loadings: NDArrayFloat,
    ) -> None:
        """Set the results of NIPALS."""
        self.rotations_matrix = rotations
        self.loadings_matrix = loadings
        self.rotations = self._to_block_type(self.rotations_matrix, "rotation")
        self.loadings = self._to_block_type(self.loadings_matrix, "loading")

    def _to_block_type(
        self,
        nipals_matrix: NDArrayFloat,
        title: str,
    ) -> BlockType:
        # Each column of the matrix generated by NIPALS corresponds to
        # an obsevation or direction. Therefore, they must be transposed
        # so that each row corresponds ot an observation or direction
        if isinstance(self.data, FDataGrid):
            return self.data.copy(
                data_matrix=nipals_matrix.T,
                sample_names=[
                    f"{title.capitalize()} {i}"
                    for i in range(self.n_components)
                ],
                coordinate_names=(f"FPLS {self.label} {title} value",),
                dataset_name=f"FPLS {self.label} {title}s",
            )
        elif isinstance(self.data, FDataBasis):
            return self.data.copy(
                coefficients=nipals_matrix.T,
                sample_names=[
                    f"{title.capitalize()} {i}"
                    for i in range(self.n_components)
                ],
                coordinate_names=(f"FPLS {self.label} {title} value",),
                dataset_name=f"FPLS {self.label} {title}s",
            )
        elif isinstance(self.data, np.ndarray):
            return cast(BlockType, nipals_matrix.T)

        raise NotImplementedError(
            f"Data type {type(self.data)} not supported",
        )

    def transform(
        self,
        data: BlockType,
    ) -> NDArrayFloat:
        """Transform from the data space to the component space."""
        if isinstance(data, FDataGrid):
            data_grid = data - self.mean
            return (
                data_grid.data_matrix[..., 0]
                @ self.G_data_weights
                @ self.rotations_matrix
            )
        elif isinstance(data, FDataBasis):
            data_basis = data - cast(FDataBasis, self.mean)
            return (
                data_basis.coefficients
                @ self.G_data_weights
                @ self.rotations_matrix
            )
        elif isinstance(data, np.ndarray):
            data_array = data
            if len(data_array.shape) == 1:
                data_array = data_array[:, np.newaxis]
            data_array = data_array - self.mean
            return data_array @ self.rotations_matrix

        raise NotImplementedError(
            f"Data type {type(data)} not supported",
        )

    def inverse_transform(
        self,
        components: NDArrayFloat,
    ) -> BlockType:
        """Transform from the component space to the data space."""
        if isinstance(self.data, FDataGrid):
            reconstructed_grid = self.data.copy(
                data_matrix=components @ self.loadings_matrix.T,
                sample_names=(None,) * components.shape[0],
                dataset_name=f"FPLS {self.label} inverse transformed",
            )
            return reconstructed_grid + cast(FDataGrid, self.mean)

        elif isinstance(self.data, FDataBasis):
            reconstructed_basis = self.data.copy(
                coefficients=components @ self.loadings_matrix.T,
                sample_names=(None,) * components.shape[0],
                dataset_name=f"FPLS {self.label} inverse transformed",
            )
            return reconstructed_basis + cast(FDataBasis, self.mean)

        elif isinstance(self.data, np.ndarray):
            reconstructed = components @ self.loadings_matrix.T
            reconstructed += self.mean
            return cast(BlockType, reconstructed)

        raise NotImplementedError(
            f"Data type {type(self.data)} not supported",
        )

    def get_penalty_matrix(self) -> NDArrayFloat:
        """Return the penalty matrix."""
        return self.G_weights + self.regularization_matrix

    def get_cholesky_inv_penalty_matrix(self) -> NDArrayFloat:
        """Return the Cholesky decomposition of the penalty matrix."""
        return np.linalg.inv(np.linalg.cholesky(self.get_penalty_matrix()))


InputTypeX = TypeVar(
    "InputTypeX",
    bound=Union[FDataGrid, FDataBasis, NDArrayFloat],
)
InputTypeY = TypeVar(
    "InputTypeY",
    bound=Union[FDataGrid, FDataBasis, NDArrayFloat],
)


# Ignore too many public instance attributes
class FPLS(  # noqa: WPS230
    BaseEstimator,
    Generic[InputTypeX, InputTypeY],
):
    r"""
    Functional Partial Least Squares Regression.

    This is a generic class. When instantiated, the type of the
    data in each block can be specified. The possiblities are:
    NDArrayFloat, FDataGrid and FDataBasis.

    Parameters:
        n_components: Number of components to extract.
        regularization_X: Regularization to apply to the X block.
        regularization_Y: Regularization to apply to the Y block.
        component_basis_X: Basis to use for the X block. Only
            applicable if X is a FDataBasis. Otherwise it must be None.
        component_basis_Y: Basis to use for the Y block. Only
            applicable if Y is a FDataBasis. Otherwise it must be None.
        _deflation_mode: Mode to use for deflation. Can be "can"
            (dimensionality reduction) or "reg" (regression).
        _integration_weights_X: One-dimensional array with the integration
            weights for the X block.
            Only applicable if X is a FDataGrid. Otherwise it must be None.
        _integration_weights_Y: One-dimensional array with the integration
            weights for the Y block.
            Only applicable if Y is a FDataGrid. Otherwise it must be None.

    Attributes:
        x_weights\_: (n_features_X, n_components) array with the X weights
            extracted by NIPALS.
        y_weights\_: (n_features_Y, n_components) array with the Y weights
            extracted by NIPALS.
        x_scores\_: (n_samples, n_components) array with the X scores
            extracted by NIPALS.
        y_scores\_: (n_samples, n_components) array with the Y scores
            extracted by NIPALS.
        x_rotations_matrix\_: (n_features_X, n_components) array with the
            X rotations.
        y_rotations_matrix\_: (n_features_Y, n_components) array with the
            Y rotations.
        x_loadings_matrix\_: (n_features_X, n_components) array with the
            X loadings.
        y_loadings_matrix\_: (n_features_Y, n_components) array with the
            Y loadings.
        x_rotations\_: Projection directions for the X block (same type as X).
        y_rotations\_: Projection directions for the Y block (same type as Y).
        x_loadings\_: Loadings for the X block (same type as X).
        y_loadings\_: Loadings for the Y block (same type as Y).

    """

    def __init__(
        self,
        n_components: int = 5,
        *,
        regularization_X: L2Regularization[InputTypeX] | None = None,
        regularization_Y: L2Regularization[InputTypeY] | None = None,
        component_basis_X: Basis | None = None,
        component_basis_Y: Basis | None = None,
        tol: float = 1e-6,
        max_iter: int = 500,
        _deflation_mode: str = "can",
        _integration_weights_X: NDArrayFloat | None = None,
        _integration_weights_Y: NDArrayFloat | None = None,
    ) -> None:
        self.n_components = n_components
        self._integration_weights_X = _integration_weights_X
        self._integration_weights_Y = _integration_weights_Y
        self.regularization_X = regularization_X
        self.regularization_Y = regularization_Y
        self.component_basis_X = component_basis_X
        self.component_basis_Y = component_basis_Y
        self._deflation_mode = _deflation_mode
        self.tol = tol
        self.max_iter = max_iter

    def _initialize_blocks(self, X: InputTypeX, Y: InputTypeY) -> None:
        self._x_block = _FPLSBlock(
            data=X,
            n_components=self.n_components,
            label="X",
            integration_weights=self._integration_weights_X,
            regularization=self.regularization_X,
            weights_basis=self.component_basis_X,
        )
        self._y_block = _FPLSBlock(
            data=Y,
            n_components=self.n_components,
            label="Y",
            integration_weights=self._integration_weights_Y,
            regularization=self.regularization_Y,
            weights_basis=self.component_basis_Y,
        )

    def _perform_nipals(self) -> None:
        X = self._x_block.data_matrix
        Y = self._y_block.data_matrix
        X = X - np.mean(X, axis=0)
        Y = Y - np.mean(Y, axis=0)

        if len(Y.shape) == 1:
            Y = Y[:, np.newaxis]

        # Store the matrices as list of columns
        W, C = [], []
        T, U = [], []
        P, Q = [], []

        # Calculate the penalty matrices in advance
        L_X_inv = self._x_block.get_cholesky_inv_penalty_matrix()
        L_Y_inv = self._y_block.get_cholesky_inv_penalty_matrix()

        for _ in range(self.n_components):
            w, c = _calculate_weights(
                X,
                Y,
                G_ww=self._x_block.G_weights,
                G_xw=self._x_block.G_data_weights,
                G_cc=self._y_block.G_weights,
                G_yc=self._y_block.G_data_weights,
                L_X_inv=L_X_inv,
                L_Y_inv=L_Y_inv,
                tol=self.tol,
                max_iter=self.max_iter,
            )

            t = np.dot(X @ self._x_block.G_data_weights, w)
            u = np.dot(Y @ self._y_block.G_data_weights, c)

            p = np.dot(X.T, t) / (np.dot(t.T, t) + INV_EPS)

            y_proyection = t if self._deflation_mode == "reg" else u

            q = np.dot(Y.T, y_proyection) / (
                np.dot(y_proyection, y_proyection) + INV_EPS
            )

            X = X - np.outer(t, p)
            Y = Y - np.outer(y_proyection, q)

            W.append(w)
            C.append(c)
            T.append(t)
            U.append(u)
            P.append(p)
            Q.append(q)

        # Convert each list of columns to a matrix
        self.x_weights_ = np.array(W).T
        self.y_weights_ = np.array(C).T
        self.x_scores_ = np.array(T).T
        self.y_scores_ = np.array(U).T
        self.x_loadings_matrix_ = np.array(P).T
        self.y_loadings_matrix_ = np.array(Q).T

    def fit(
        self,
        X: InputTypeX,
        y: InputTypeY,
    ) -> FPLS[InputTypeX, InputTypeY]:
        """
        Fit the model using the data for both blocks.

        Args:
            X: Data of the X block
            y: Data of the Y block

        Returns:
            self
        """
        self._initialize_blocks(X, y)

        self._perform_nipals()

        self.x_rotations_matrix_ = self.x_weights_ @ np.linalg.pinv(
            self.x_loadings_matrix_.T
            @ self._x_block.G_data_weights
            @ self.x_weights_,
        )

        self.y_rotation_matrix_ = self.y_weights_ @ np.linalg.pinv(
            self.y_loadings_matrix_.T
            @ self._y_block.G_data_weights
            @ self.y_weights_,
        )

        self._x_block.set_nipals_results(
            rotations=self.x_rotations_matrix_,
            loadings=self.x_loadings_matrix_,
        )
        self._y_block.set_nipals_results(
            rotations=self.y_rotation_matrix_,
            loadings=self.y_loadings_matrix_,
        )

        self.x_rotations_ = self._x_block.rotations
        self.y_rotations_ = self._y_block.rotations

        self.x_loadings_ = self._x_block.loadings
        self.y_loadings_ = self._y_block.loadings

        return self

    def transform(
        self,
        X: InputTypeX,
        y: InputTypeY | None = None,
    ) -> NDArrayFloat | Tuple[NDArrayFloat, NDArrayFloat]:
        """
        Apply the dimension reduction learned on the train data.

        Args:
            X: Data to transform. Must have the same number of features and
                type as the data used to train the model.
            y : Data to transform. Must have the same number of features and
                type as the data used to train the model.
                If None, only X is transformed.

        Returns:
            - x_scores: Data transformed.
            - y_scores: Data transformed (if y is not None)
        """
        check_is_fitted(self)

        x_scores = self._x_block.transform(X)

        if y is not None:
            y_scores = self._y_block.transform(y)
            return x_scores, y_scores

        return x_scores

    def transform_x(
        self,
        X: InputTypeX,
    ) -> NDArrayFloat:
        """
        Apply the dimension reduction learned on the train data.

        Args:
            X: Data to transform. Must have the same number of features and
                type as the data used to train the model.


        Returns:
            - Data transformed.
        """
        check_is_fitted(self)

        return self._x_block.transform(X)

    def transform_y(
        self,
        Y: InputTypeY,
    ) -> NDArrayFloat:
        """
        Apply the dimension reduction learned on the train data.

        Args:
            Y: Data to transform. Must have the same number of features and
                type as the data used to train the model.


        Returns:
            - Data transformed.
        """
        check_is_fitted(self)

        return self._y_block.transform(Y)

    def inverse_transform(
        self,
        X: NDArrayFloat,
        Y: NDArrayFloat | None = None,
    ) -> InputTypeX | Tuple[InputTypeX, InputTypeY]:
        """
        Transform data back to its original space.

        Args:
            X: Data to transform back. Must have the same number of columns
                as the number of components of the model.
            Y: Data to transform back. Must have the same number of columns
                as the number of components of the model.

        Returns:
            - X: Data reconstructed from the transformed data.
            - Y: Data reconstructed from the transformed data
                (if Y is not None)

        """
        check_is_fitted(self)

        x_reconstructed = self._x_block.inverse_transform(X)

        if Y is not None:
            y_reconstructed = self._y_block.inverse_transform(Y)
            return x_reconstructed, y_reconstructed

        return x_reconstructed

    def inverse_transform_x(
        self,
        X: NDArrayFloat,
    ) -> InputTypeX:
        """
        Transform X data back to its original space.

        Args:
            X: Data to transform back. Must have the same number of columns
                as the number of components of the model.

        Returns:
            - Data reconstructed from the transformed data.
        """
        check_is_fitted(self)

        return self._x_block.inverse_transform(X)

    def inverse_transform_y(
        self,
        Y: NDArrayFloat,
    ) -> InputTypeY:
        """
        Transform Y data back to its original space.

        Args:
            Y: Data to transform back. Must have the same number of columns
                as the number of components of the model.

        Returns:
            - Data reconstructed from the transformed data.
        """
        check_is_fitted(self)

        return self._y_block.inverse_transform(Y)
