import copy
import itertools
from functools import partial
from typing import (
    Generator,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    cast,
)

import numpy as np
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.backend_bases import Event, LocationEvent, MouseEvent
from matplotlib.collections import PathCollection
from matplotlib.figure import Figure
from matplotlib.text import Annotation
from matplotlib.widgets import Slider, Widget

from ._baseplot import BasePlot
from ._utils import _get_axes_shape, _get_figure_and_axes, _set_figure_layout


def _set_val_noevents(widget: Widget, val: float) -> None:
    e = widget.eventson
    widget.eventson = False
    widget.set_val(val)
    widget.eventson = e


class MultipleDisplay:
    """
    MultipleDisplay class used to combine and interact with plots.

    This module is used to combine different BasePlot objects that
    represent the same curves or surfaces, and represent them
    together in the same figure. Besides this, it includes
    the functionality necessary to interact with the graphics
    by clicking the points, hovering over them... Picking the points allow
    us to see our selected function standing out among the others in all
    the axes. It is also possible to add widgets to interact with the
    plots.
    Args:
        displays: Baseplot objects that will be plotted in the fig.
        criteria: Sequence of criteria used to order the points in the
            slider widget. The size should be equal to sliders, as each
            criterion is for one slider.
        sliders: Sequence of widgets that will be plotted.
        label_sliders: Label of each of the sliders.
        chart: Figure over with the graphs are plotted or axis over
            where the graphs are plotted. If None and ax is also
            None, the figure is initialized.
        fig: Figure over with the graphs are plotted in case ax is not
            specified. If None and ax is also None, the figure is
            initialized.
        axes: Axis where the graphs are plotted. If None, see param fig.
    Attributes:
        length_data: Number of instances or curves of the different displays.
        clicked: Boolean indicating whether a point has being clicked.
        selected_sample: Index of the function selected with the interactive
            module or widgets.
    """

    def __init__(
        self,
        displays: Union[BasePlot, Sequence[BasePlot]],
        criteria: Union[Sequence[float], Sequence[Sequence[float]]] = (),
        sliders: Union[Type[Widget], Sequence[Type[Widget]]] = (),
        label_sliders: Union[str, Sequence[str], None] = None,
        chart: Union[Figure, Axes, None] = None,
        fig: Optional[Figure] = None,
        axes: Optional[Sequence[Axes]] = None,
    ):
        if isinstance(displays, BasePlot):
            displays = (displays,)

        self.displays = [copy.copy(d) for d in displays]
        self._n_graphs = sum(d.n_subplots for d in self.displays)
        self.length_data = next(
            d.n_samples
            for d in self.displays
            if d.n_samples is not None
        )
        self.sliders: List[Widget] = []
        self.criteria: List[List[int]] = []
        self.selected_sample: Optional[int] = None
        self._tag = self._create_annotation()

        if len(criteria) != 0 and not isinstance(criteria[0], Sequence):
            criteria = (criteria,)

        criteria = cast(Sequence[Sequence[float]], criteria)

        if not isinstance(sliders, Sequence):
            sliders = (sliders,)

        if isinstance(label_sliders, str):
            label_sliders = (label_sliders,)

        if len(criteria) != len(sliders):
            raise ValueError(
                f"Size of criteria, and sliders should be equal "
                f"(have {len(criteria)} and {len(sliders)}).",
            )

        self._init_axes(
            chart,
            fig=fig,
            axes=axes,
            extra=len(criteria),
        )

        self._create_sliders(
            criteria=criteria,
            sliders=sliders,
            label_sliders=label_sliders,
        )

    def _init_axes(
        self,
        chart: Union[Figure, Axes, None] = None,
        *,
        fig: Optional[Figure] = None,
        axes: Optional[Sequence[Axes]] = None,
        extra: int = 0,
    ) -> None:
        """
        Initialize the axes and figure.

        Args:
            chart: Figure over with the graphs are plotted or axis over
                where the graphs are plotted. If None and ax is also
                None, the figure is initialized.
            fig: Figure over with the graphs are plotted in case ax is not
                specified. If None and ax is also None, the figure is
                initialized.
            axes: Axis where the graphs are plotted. If None, see param fig.
            extra: integer indicating the extra axes needed due to the
                necessity for them to plot the sliders.

        """
        widget_aspect = 1 / 8
        fig, axes = _get_figure_and_axes(chart, fig, axes)
        if len(axes) not in {0, self._n_graphs + extra}:
            raise ValueError("Invalid number of axes.")

        n_rows, n_cols = _get_axes_shape(self._n_graphs + extra)

        dim = list(
            itertools.chain.from_iterable(
                [d.dim] * d.n_subplots
                for d in self.displays
            ),
        ) + [2] * extra

        number_axes = n_rows * n_cols
        fig, axes = _set_figure_layout(
            fig=fig,
            axes=axes,
            n_axes=self._n_graphs + extra,
            dim=dim,
        )

        for i in range(self._n_graphs, number_axes):
            if i >= self._n_graphs + extra:
                axes[i].set_visible(False)
            else:
                axes[i].set_box_aspect(widget_aspect)

        self.fig = fig
        self.axes = axes

    def _create_sliders(
        self,
        *,
        criteria: Sequence[Sequence[float]],
        sliders: Sequence[Type[Widget]],
        label_sliders: Optional[Sequence[str]] = None,
    ) -> None:
        """
        Create the sliders with the criteria selected.

        Args:
            criteria: Different criterion for each of the sliders.
            sliders: Widget types.
            label_sliders: Sequence of the names of each slider.

        """
        for c in criteria:
            if len(c) != self.length_data:
                raise ValueError(
                    "Slider criteria should be of the same size as data",
                )

        for k, criterion in enumerate(criteria):
            label = label_sliders[k] if label_sliders else None

            self.add_slider(
                axes=self.axes[self._n_graphs + k],
                criterion=criterion,
                widget_class=sliders[k],
                label=label,
            )

    def _create_annotation(self) -> Annotation:
        tag = Annotation(
            "",
            xy=(0, 0),
            xytext=(20, 20),
            textcoords="offset points",
            bbox={
                "boxstyle": "round",
                "fc": "w",
            },
            arrowprops={
                "arrowstyle": "->",
            },
        )

        tag.get_bbox_patch().set_facecolor(color='khaki')
        intensity = 0.8
        tag.get_bbox_patch().set_alpha(intensity)

        return tag

    def _update_annotation(
        self,
        tag: Annotation,
        *,
        axes: Axes,
        sample_number: int,
        position: Tuple[float, float],
    ) -> None:
        """
        Auxiliary method used to update the hovering annotations.

        Method used to update the annotations that appear while
        hovering a scattered point. The annotations indicate
        the index and coordinates of the point hovered.
        Args:
            tag: Annotation to update.
            axes: Axes were the annotation belongs.
            sample_number: Number of the current sample.
        """
        xdata_graph, ydata_graph = position

        tag.xy = (xdata_graph, ydata_graph)
        text = f"{sample_number}: ({xdata_graph:.2f}, {ydata_graph:.2f})"
        tag.set_text(text)

        x_axis = axes.get_xlim()
        y_axis = axes.get_ylim()

        label_xpos = 20
        label_ypos = 20
        if (xdata_graph - x_axis[0]) > (x_axis[1] - xdata_graph):
            label_xpos = -80

        if (ydata_graph - y_axis[0]) > (y_axis[1] - ydata_graph):
            label_ypos = -20

        if tag.figure:
            tag.remove()
        tag.figure = None
        axes.add_artist(tag)
        tag.set_transform(axes.transData)
        tag.set_position((label_xpos, label_ypos))

    def plot(self) -> Figure:
        """
        Plot Multiple Display method.

        Plot the different BasePlot objects and widgets selected.
        Activates the interactivity functionality of clicking and
        hovering points. When clicking a point, the rest will be
        made partially transparent in all the corresponding graphs.
        Returns:
            fig: figure object in which the displays and
                widgets will be plotted.
        """
        if self._n_graphs > 1:
            for d in self.displays[1:]:
                if (
                    d.n_samples is not None
                    and d.n_samples != self.length_data
                ):
                    raise ValueError(
                        "Length of some data sets are not equal ",
                    )

        for ax in self.axes[:self._n_graphs]:
            ax.clear()

        int_index = 0
        for disp in self.displays:
            axes_needed = disp.n_subplots
            end_index = axes_needed + int_index
            disp._set_figure_and_axes(axes=self.axes[int_index:end_index])
            disp.plot()
            int_index = end_index

        self.fig.canvas.mpl_connect('motion_notify_event', self.hover)
        self.fig.canvas.mpl_connect('pick_event', self.pick)

        self._tag.set_visible(False)

        self.fig.suptitle("Multiple display")
        self.fig.tight_layout()

        return self.fig

    def _sample_artist_from_event(
        self,
        event: LocationEvent,
    ) -> Optional[Tuple[int, Artist]]:
        """Get the number of sample and artist under a location event."""
        for d in self.displays:
            if d.artists is None:
                continue

            try:
                i = d.axes_.index(event.inaxes)
            except ValueError:
                continue

            for j, artist in enumerate(d.artists[:, i]):
                if not isinstance(artist, PathCollection):
                    return None

                if artist.contains(event)[0]:
                    return j, artist

        return None

    def hover(self, event: MouseEvent) -> None:
        """
        Activate the annotation when hovering a point.

        Callback method that activates the annotation when hovering
        a specific point in a graph. The annotation is a description
        of the point containing its coordinates.
        Args:
            event: event object containing the artist of the point
                hovered.

        """
        found_artist = self._sample_artist_from_event(event)

        if event.inaxes is not None and found_artist is not None:
            sample_number, artist = found_artist

            self._update_annotation(
                self._tag,
                axes=event.inaxes,
                sample_number=sample_number,
                position=artist.get_offsets()[0],
            )
            self._tag.set_visible(True)
            self.fig.canvas.draw_idle()
        elif self._tag.get_visible():
            self._tag.set_visible(False)
            self.fig.canvas.draw_idle()

    def pick(self, event: Event) -> None:
        """
        Activate interactive functionality when picking a point.

        Callback method that is activated when a point is picked.
        If no point was clicked previously, all the points but the
        one selected will be more transparent in all the graphs.
        If a point was clicked already, this new point will be the
        one highlighted among the rest. If the same point is clicked,
        the initial state of the graphics is restored.
        Args:
            event: event object containing the artist of the point
                picked.
        """
        selected_sample = self._sample_from_artist(event.artist)

        if selected_sample is not None:
            if self.selected_sample == selected_sample:
                self._deselect_samples()
            else:
                self._select_sample(selected_sample)

    def _sample_from_artist(self, artist: Artist) -> Optional[int]:
        """Return the sample corresponding to an artist."""
        for d in self.displays:

            if d.artists is None:
                continue

            for i, a in enumerate(d.axes_):
                if a == artist.axes:
                    if len(d.axes_) == 1:
                        return np.where(d.artists == artist)[0][0]
                    else:
                        return np.where(d.artists[:, i] == artist)[0][0]

        return None

    def _visit_artists(self) -> Generator[Tuple[int, Artist], None, None]:
        for i in range(self.length_data):
            for d in self.displays:
                if d.artists is None:
                    continue

                yield from ((i, artist) for artist in np.ravel(d.artists[i]))

    def _select_sample(self, selected_sample: int) -> None:
        """Reduce the transparency of all the points but the selected one."""
        for i, artist in self._visit_artists():
            artist.set_alpha(1.0 if i == selected_sample else 0.1)

        for criterion, slider in zip(self.criteria, self.sliders):
            val_widget = criterion.index(selected_sample)
            _set_val_noevents(slider, val_widget)

        self.selected_sample = selected_sample
        self.fig.canvas.draw_idle()

    def _deselect_samples(self) -> None:
        """Restore the original transparency of all the points."""
        for _, artist in self._visit_artists():
            artist.set_alpha(1)

        self.selected_sample = None
        self.fig.canvas.draw_idle()

    def add_slider(
        self,
        axes: Axes,
        criterion: Sequence[float],
        widget_class: Type[Widget] = Slider,
        label: Optional[str] = None,
    ) -> None:
        """
        Add the slider to the MultipleDisplay object.

        Args:
            axes: Axes for the widget.
            criterion: Criterion used for the slider.
            widget_class: Widget type.
            label: Name of the slider.
        """
        full_desc = "" if label is None else label

        widget = widget_class(
            ax=axes,
            label=full_desc,
            valmin=0,
            valmax=self.length_data - 1,
            valinit=0,
            valstep=1,
        )

        self.sliders.append(widget)

        axes.annotate(
            '0',
            xy=(0, -0.5),
            xycoords='axes fraction',
            annotation_clip=False,
        )

        axes.annotate(
            str(self.length_data - 1),
            xy=(0.95, -0.5),
            xycoords='axes fraction',
            annotation_clip=False,
        )

        criterion_sample_indexes = [
            x for _, x in sorted(zip(criterion, range(self.length_data)))
        ]

        self.criteria.append(criterion_sample_indexes)

        on_changed_function = partial(
            self._value_updated,
            criterion_sample_indexes=criterion_sample_indexes,
        )

        widget.on_changed(on_changed_function)

    def _value_updated(
        self,
        value: int,
        criterion_sample_indexes: Sequence[int],
    ) -> None:
        """
        Update the graphs when a widget is clicked.

        Args:
            value: Current value of the widget.
            criterion_sample_indexes: Sample numbers ordered using the
                criterion.

        """
        self.selected_sample = criterion_sample_indexes[value]
        self._select_sample(self.selected_sample)