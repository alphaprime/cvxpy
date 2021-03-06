"""
Copyright 2013 Steven Diamond

This file is part of CVXPY.

CVXPY is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

CVXPY is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with CVXPY.  If not, see <http://www.gnu.org/licenses/>.
"""

from cvxpy.error import DCPError
import cvxpy.utilities as u
import cvxpy.interface as intf
import cvxpy.utilities.key_utils as ku
import cvxpy.settings as s
from cvxpy.utilities import performance_utils as pu
from cvxpy.constraints import EqConstraint, LeqConstraint, PSDConstraint
from cvxpy.expressions import types
import abc
import numpy as np

def _cast_other(binary_op):
    """Casts the second argument of a binary operator as an Expression.

    Args:
        binary_op: A binary operator in the Expression class.

    Returns:
        A wrapped binary operator that can handle non-Expression arguments.
    """
    def cast_op(self, other):
        """A wrapped binary operator that can handle non-Expression arguments.
        """
        other = self.cast_to_const(other)
        return binary_op(self, other)
    return cast_op

class Expression(u.Canonical):
    """
    A mathematical expression in a convex optimization problem.
    """

    __metaclass__ = abc.ABCMeta

    # Handles arithmetic operator overloading with Numpy.
    __array_priority__ = 100

    @abc.abstractproperty
    def value(self):
        """Returns the numeric value of the expression.

        Returns:
            A numpy matrix or a scalar.
        """
        return NotImplemented

    @abc.abstractproperty
    def grad(self):
        """Gives the (sub/super)gradient of the expression w.r.t. each variable.

        Matrix expressions are vectorized, so the gradient is a matrix.

        Returns:
            A map of variable to SciPy CSC sparse matrix.
            None if a variable value is missing.
        """
        return NotImplemented

    @abc.abstractproperty
    def domain(self):
        """A list of constraints describing the closure of the region
           where the expression is finite.
        """
        return NotImplemented

    def __str__(self):
        """Returns a string showing the mathematical expression.
        """
        return self.name()

    def __repr__(self):
        """Returns a string with information about the expression.
        """
        return "Expression(%s, %s, %s)" % (self.curvature,
                                           self.sign,
                                           self.size)

    @abc.abstractmethod
    def name(self):
        """Returns the string representation of the expression.
        """
        return NotImplemented

    # Curvature properties.

    @property
    def curvature(self):
        """ Returns the curvature of the expression.
        """
        if self.is_constant():
            curvature_str = s.CONSTANT
        elif self.is_affine():
            curvature_str = s.AFFINE
        elif self.is_convex():
            curvature_str = s.CONVEX
        elif self.is_concave():
            curvature_str = s.CONCAVE
        else:
            curvature_str = s.UNKNOWN
        return curvature_str

    def is_constant(self):
        """Is the expression constant?
        """
        return len(self.variables()) == 0 or self.is_zero()

    def is_affine(self):
        """Is the expression affine?
        """
        return self.is_constant() or (self.is_convex() and self.is_concave())

    @abc.abstractmethod
    def is_convex(self):
        """Is the expression convex?
        """
        return NotImplemented

    @abc.abstractmethod
    def is_concave(self):
        """Is the expression concave?
        """
        return NotImplemented

    def is_dcp(self):
        """Is the expression DCP compliant? (i.e., no unknown curvatures).
        """
        return self.is_convex() or self.is_concave()

    # Sign properties.

    @property
    def sign(self):
        """Returns the sign of the expression.
        """
        if self.is_zero():
            sign_str = s.ZERO
        elif self.is_positive():
            sign_str = s.POSITIVE
        elif self.is_negative():
            sign_str = s.NEGATIVE
        else:
            sign_str = s.UNKNOWN
        return sign_str

    def is_zero(self):
        """Is the expression all zero?
        """
        return self.is_positive() and self.is_negative()

    @abc.abstractmethod
    def is_positive(self):
        """Is the expression positive?
        """
        return NotImplemented

    @abc.abstractmethod
    def is_negative(self):
        """Is the expression negative?
        """
        return NotImplemented

    @abc.abstractproperty
    def size(self):
        """Returns the (row, col) dimensions of the expression.
        """
        return NotImplemented

    def is_scalar(self):
        """Is the expression a scalar?
        """
        return self.size == (1, 1)

    def is_vector(self):
        """Is the expression a column or row vector?
        """
        return min(self.size) == 1

    def is_matrix(self):
        """Is the expression a matrix?
        """
        return self.size[0] > 1 and self.size[1] > 1

    def __getitem__(self, key):
        """Return a slice/index into the expression.
        """
        # Returning self for scalars causes
        # the built-in sum to hang.
        if ku.is_special_slice(key):
            return types.index().get_special_slice(self, key)
        else:
            return types.index()(self, key)

    @property
    def T(self):
        """The transpose of an expression.
        """
        # Transpose of a scalar is that scalar.
        if self.is_scalar():
            return self
        else:
            return types.transpose()(self)

    def __pow__(self, power):
        """The power operator.
        """
        return types.power()(self, power)

    # Arithmetic operators.
    @staticmethod
    def cast_to_const(expr):
        """Converts a non-Expression to a Constant.
        """
        return expr if isinstance(expr, Expression) else types.constant()(expr)

    @_cast_other
    def __add__(self, other):
        """The sum of two expressions.
        """
        return types.add_expr()([self, other])

    @_cast_other
    def __radd__(self, other):
        """Called for Number + Expression.
        """
        return other + self

    @_cast_other
    def __sub__(self, other):
        """The difference of two expressions.
        """
        return self + -other

    @_cast_other
    def __rsub__(self, other):
        """Called for Number - Expression.
        """
        return other - self

    @_cast_other
    def __mul__(self, other):
        """The product of two expressions.
        """
        # Cannot multiply two non-constant expressions.
        if not self.is_constant() and \
           not other.is_constant():
            raise DCPError("Cannot multiply two non-constants.")
        # Multiplying by a constant on the right is handled differently
        # from multiplying by a constant on the left.
        elif self.is_constant():
            # TODO HACK catch c.T*x where c is a NumPy 1D array.
            if self.size[0] == other.size[0] and \
               self.size[1] != self.size[0] and \
               isinstance(self, types.constant()) and self.is_1D_array:
                self = self.T
            return types.mul_expr()(self, other)
        # Having the constant on the left is more efficient.
        elif self.is_scalar() or other.is_scalar():
            return types.mul_expr()(other, self)
        else:
            return types.rmul_expr()(self, other)

    @_cast_other
    def __truediv__(self, other):
        """One expression divided by another.
        """
        return self.__div__(other)

    @_cast_other
    def __div__(self, other):
        """One expression divided by another.
        """
        # Can only divide by scalar constants.
        if other.is_constant() and other.is_scalar():
            return types.div_expr()(self, other)
        else:
            raise DCPError("Can only divide by a scalar constant.")

    @_cast_other
    def __rdiv__(self, other):
        """Called for Number / Expression.
        """
        return other / self

    @_cast_other
    def __rtruediv__(self, other):
        """Called for Number / Expression.
        """
        return other / self

    @_cast_other
    def __rmul__(self, other):
        """Called for Number * Expression.
        """
        return other * self

    def __neg__(self):
        """The negation of the expression.
        """
        return types.neg_expr()(self)

    @_cast_other
    def __rshift__(self, other):
        """Positive definite inequality.
        """
        return PSDConstraint(self, other)

    @_cast_other
    def __rrshift__(self, other):
        """Positive definite inequality.
        """
        return PSDConstraint(other, self)

    @_cast_other
    def __lshift__(self, other):
        """Positive definite inequality.
        """
        return PSDConstraint(other, self)

    @_cast_other
    def __rlshift__(self, other):
        """Positive definite inequality.
        """
        return PSDConstraint(self, other)

    # Needed for Python3:
    def __hash__(self):
        return id(self)

    # Comparison operators.
    @_cast_other
    def __eq__(self, other):
        """Returns an equality constraint.
        """
        return EqConstraint(self, other)

    @_cast_other
    def __le__(self, other):
        """Returns an inequality constraint.
        """
        return LeqConstraint(self, other)

    def __lt__(self, other):
        """Returns an inequality constraint.
        """
        return self <= other

    @_cast_other
    def __ge__(self, other):
        """Returns an inequality constraint.
        """
        return other.__le__(self)

    def __gt__(self, other):
        """Returns an inequality constraint.
        """
        return self >= other
